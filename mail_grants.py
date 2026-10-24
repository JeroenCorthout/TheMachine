"""Google, Microsoft and Zoom sign-ins of broker accounts, kept on this Machine only (docs/configuration.md, "Broker accounts").

A hosting control plane keeps the OAuth client and its secret, never the sign-ins themselves. A broker account's refresh
token lives here, in `mail-grants.json` next to the database (mode 600), sealed with the message key (message_seal.py).
The app sends it along with each request for an access token (google_api.py, ms_graph.py); the control plane asks
Google or Microsoft with its client secret and answers with the access token (and Microsoft's rotated refresh token,
saved here), keeping neither. Disconnecting revokes a Google sign-in at Google from here and forgets it; Microsoft has
no way to revoke one, so it is forgotten (the owner can remove the app at Microsoft too).

Each disconnect leaves a receipt (`revocations.json` next to it: provider, address, when, no token), which Connections
shows: "Removed, confirmed" once Google confirmed the revoke (Microsoft and Zoom: once no copy is left here), else
"Pending" with why. A revoke Google didn't confirm keeps its token sealed under `revoke:<provider>:<address>`, used for
nothing but trying again (`retry`: every RETRY_MINUTES from the app, and when the control plane asks), until Google
answers.

An account connected before this has its sign-in at the control plane until it is moved here; until then the app asks
without one, as before."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import message_seal
import portable

FILE = 'mail-grants.json'
RECEIPTS = 'revocations.json'
PENDING = 'revoke:'  # a sign-in forgotten here whose revoke Google hasn't confirmed yet: kept only to try again
RECEIPT_DAYS = 30
RETRY_MINUTES = 60
PROVIDERS = ('google', 'microsoft', 'zoom')  # Zoom: connectors/zoom.py, forgotten here (the owner can remove the app at Zoom)
GOOGLE_REVOKE = 'https://oauth2.googleapis.com/revoke'
GOOGLE_REMOVE = 'myaccount.google.com/permissions'
MICROSOFT_REMOVE = 'myapps.microsoft.com (work) or account.live.com/consent/Manage (personal)'


class GrantError(Exception):
    """The kept sign-ins can't be read or written (the message key has not arrived, or the file is damaged)."""


def _database(config):
    if not config.get('database'):
        raise GrantError('no database in the config')
    return Path(config['database']).expanduser()


def path(config):
    return _database(config).parent / FILE


def _sealer(config):
    try:
        key = message_seal.load_key(_database(config), create=True)
    except (message_seal.SealError, OSError) as error:
        raise GrantError('the message key is not here yet') from error
    return message_seal.Sealer(key)


def _name(provider, email):
    if provider not in PROVIDERS:
        raise ValueError(provider)
    return f'{provider}:{str(email).strip().lower()}'


def _read(config):
    try:
        data = json.loads(path(config).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise GrantError(f'{FILE} does not open') from error
    return data if isinstance(data, dict) else {}


def _write(config, data):
    target = path(config)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fresh = target.with_name(target.name + '.new')
    fd = os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(fresh, target)
    if os.name != 'nt':
        fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd)  # the handoff may only be acknowledged after its renamed file is durable
        finally:
            os.close(fd)


class _Locked:
    """Changes one at a time, across the app and the hosting agent's steps."""

    def __init__(self, config):
        self.file = path(config).with_name(FILE + '.lock')

    def __enter__(self):
        self.file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.handle = open(self.file, 'a+')
        portable.lock(self.handle)
        return self

    def __exit__(self, *exc):
        self.handle.close()


def _record(config, provider, email):
    """The refresh token kept here for this sign-in, or None (not connected here, or still at the control plane)."""
    name = _name(provider, email)
    if not config.get('database'):
        return None
    sealed = _read(config).get(name)
    if not sealed:
        return None
    try:
        kept = json.loads(_sealer(config).open(base64.b64decode(sealed)))
    except (message_seal.SealError, ValueError) as error:
        raise GrantError('a kept sign-in does not open') from error
    if kept.get('for') != name:  # sealed for another sign-in: never sent for this one
        raise GrantError('a kept sign-in is for another account')
    return kept


def get(config, provider, email):
    """The refresh token kept on this Machine, or None."""
    return (_record(config, provider, email) or {}).get('token') or None


def handed(config, provider, email, handoff):
    """Whether this exact handoff was durably saved, including after a token rotation."""
    return bool(handoff and (_record(config, provider, email) or {}).get('handoff') == handoff)


def put(config, provider, email, token, handoff=None):
    name = _name(provider, email)
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise ValueError('not a refresh token')
    sealed = base64.b64encode(_sealer(config).seal(json.dumps({'for': name, 'token': token, **({'handoff': handoff} if handoff else {})}))).decode()
    with _Locked(config):
        data = _read(config)
        data[name] = sealed
        _write(config, data)
        _receipt(config, provider, email, None)  # connected again: no longer a disconnect to show


def rotate(config, provider, email, previous, token):
    """Keep a rotated token only if the sign-in that made the request is still current.

    A slow refresh must not overwrite a later owner reconnect or resurrect a disconnected account.
    """
    with _Locked(config):
        if get(config, provider, email) != previous:
            return False
        # put uses the same file lock, so write here rather than recursively locking it.
        name = _name(provider, email)
        if not isinstance(token, str) or not token or len(token) > 4096:
            raise ValueError('not a refresh token')
        data = _read(config)
        kept = _record(config, provider, email)
        data[name] = base64.b64encode(_sealer(config).seal(json.dumps({**kept, 'token': token}))).decode()
        _write(config, data)
        return True


def has(config, provider, email):
    try:
        return bool(_read(config).get(_name(provider, email)))
    except GrantError:
        return False


def held(config):
    """(provider, email) of every sign-in kept here."""
    if not config.get('database'):
        return []
    return [tuple(name.split(':', 1)) for name in sorted(_read(config)) if not name.startswith(PENDING)]


def _revoke_google(token, timeout=20):
    request = urllib.request.Request(GOOGLE_REVOKE, data=urllib.parse.urlencode({'token': token}).encode(), method='POST',
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return True
    except urllib.error.HTTPError as error:
        error.close()
        return error.code == 400  # already revoked or expired: nothing left to revoke
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def forget(config, provider, email, revoke=True):
    """Revoke a Google sign-in at Google (Microsoft has no revoke) and forget it here. Returns 'revoked', 'forgotten'
    (nothing to revoke, or Microsoft), 'not_revoked' (Google did not answer: forgotten for every use, kept sealed only
    to try again, see `retry`) or None when nothing was kept. With revoke (a disconnect), it leaves a receipt."""
    name = _name(provider, email)
    try:
        token = get(config, provider, email)
    except GrantError:
        token = None
    outcome = None
    if token:
        outcome = 'forgotten'
        if revoke and provider == 'google':
            outcome = 'revoked' if _revoke_google(token) else 'not_revoked'
    with _Locked(config):
        data = _read(config)
        if name in data:
            outcome = outcome or 'forgotten'
            if outcome == 'not_revoked':
                data[PENDING + name] = base64.b64encode(_sealer(config).seal(json.dumps(
                    {'for': PENDING + name, 'token': token, 'tries': 1, 'tried_at': _now()}))).decode()
            data.pop(name)
            _write(config, data)
        if revoke and outcome:
            _receipt(config, provider, email, 'pending' if outcome == 'not_revoked' else 'removed')
    if revoke and outcome is None and PENDING + name in _read(config):
        retry(config, only=name)  # asked again (the control plane): what Google didn't confirm before
        outcome = 'revoked' if PENDING + name not in _read(config) else 'not_revoked'
    return outcome


def _now():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def _age_minutes(stamp):
    import datetime as dt
    try:
        return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp)).total_seconds() / 60
    except (TypeError, ValueError):
        return 1e9


def _receipts_path(config):
    return _database(config).parent / RECEIPTS


def receipts(config):
    """The disconnects of the last RECEIPT_DAYS, newest first: [{provider, email, at, state: removed or pending}]."""
    try:
        found = json.loads(_receipts_path(config).read_text())
    except (OSError, ValueError, GrantError):
        return []
    found = [r for r in found if isinstance(r, dict) and _age_minutes(r.get('at')) < RECEIPT_DAYS * 1440] if isinstance(found, list) else []
    return sorted(found, key=lambda r: r.get('at') or '', reverse=True)


def _receipt(config, provider, email, state):
    """Called with the lock held. One receipt per sign-in: a new disconnect replaces it, a retry updates its state, and
    connecting it again (state None) drops it."""
    email = str(email).strip().lower()
    kept = [r for r in receipts(config) if (r.get('provider'), r.get('email')) != (provider, email)]
    old = next((r for r in receipts(config) if (r.get('provider'), r.get('email')) == (provider, email)), None)
    if state is None and not old:
        return
    at = old['at'] if old and state == 'removed' and old.get('state') == 'pending' else _now()
    if state:
        kept.insert(0, {'provider': provider, 'email': email, 'at': at, 'state': state, **({'confirmed_at': _now()} if state == 'removed' else {})})
    target = _receipts_path(config)
    fresh = target.with_name(target.name + '.new')
    fd = os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(kept[:100], handle)
    os.replace(fresh, target)


def retry(config, only=None, due_only=False):
    """Tries again the Google revokes Google didn't confirm (all, or `only` one sign-in; due_only: those last tried
    RETRY_MINUTES ago or more). Each one Google confirms goes for good. Returns how many still wait."""
    if not config.get('database'):
        return 0
    try:
        pending = [n for n in _read(config) if n.startswith(PENDING) and (only is None or n == PENDING + only)]
    except GrantError:
        return 0
    left = 0
    for name in pending:
        provider, email = name[len(PENDING):].split(':', 1)
        with _Locked(config):
            data = _read(config)
            if name not in data:
                continue
            try:
                kept = json.loads(_sealer(config).open(base64.b64decode(data[name])))
            except (message_seal.SealError, ValueError, GrantError):
                left += 1
                continue
            if kept.get('for') != name:
                left += 1
                continue
            if due_only and _age_minutes(kept.get('tried_at')) < RETRY_MINUTES:
                left += 1
                continue
            if _revoke_google(kept.get('token') or ''):
                data.pop(name)
                _write(config, data)
                _receipt(config, provider, email, 'removed')
                continue
            data[name] = base64.b64encode(_sealer(config).seal(json.dumps(
                {**kept, 'tries': int(kept.get('tries') or 0) + 1, 'tried_at': _now()}))).decode()
            _write(config, data)
            left += 1
    return left
