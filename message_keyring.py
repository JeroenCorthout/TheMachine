"""The message key in the desktop's keyring (Secret Service) instead of a file next to the data (docs/security.md,
"Message contents at rest"). Opt-in, for a self-hosted Machine on a Linux desktop: `assistant message-key to-keyring`,
or `"message_key_store": "keyring"` in config.json, which moves it at a start once the keyring checks out.

Once moved:
- the keyring holds the key, as an item with the attributes application=the-machine, kind=message-content-key and
  id=<a random id for this Machine>;
- `message-content.keyring` (MARKER) next to the database says so: that id, the collection and a keyed hash of the key
  (`check`), so a wrong key from the keyring is never used. Nothing in it is secret;
- `message-content.key` is a link to a file in memory ($XDG_RUNTIME_DIR, gone at every reboot) that the app fills from
  the keyring when it starts, as a hosting layer does (message_seal.held_elsewhere). So the rest of the app, and the
  Gateway's plugin that seals phone notifications with it, read the key as before, and a key that can't be read is
  waited for, never made anew: a new key would leave every sealed message and saved sign-in unreadable.

The keyring may be locked, slow or not running yet (the service starts at boot, before the desktop session), and some
keyrings restart now and then. So the key is read from it once per start and kept in memory (message_seal), a
read that fails is tried again (the dashboard says why meanwhile), and the app never asks the keyring to unlock: an
unlock prompt at boot has nobody to answer it.

`assistant message-key to-file` puts the key back in the file. Both moves write the new place first, read it back,
check that it opens what is sealed now, and only then remove the old place (the file is overwritten first).

Tools: secret-tool (libsecret) stores and reads the secret; busctl (systemd) asks whether the item is there and
unlocked without ever causing a prompt. Both come with a systemd desktop (Ubuntu: apt install libsecret-tools);
nothing to pip install, as the rest of the app."""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

import message_seal

MARKER = 'message-content.keyring'
MOVING = 'message-content.key.moving'  # the old file between the switch and its overwrite (a crash leaves it)
LABEL = 'The Machine message key'
KIND = 'message-content-key'
PROBE = 'probe'
SERVICE = 'org.freedesktop.secrets'
TIMEOUT = 10
RETRY_AFTER = 30  # a read that failed is tried again after this long (the service's start waits and tries sooner)


class Unavailable(Exception):
    """The keyring can't give the key now. `reason`: no-tools, no-runtime, no-service, locked, missing, timeout,
    failed, wrong-key."""

    def __init__(self, reason, detail=''):
        super().__init__(detail or REASONS.get(reason, reason))
        self.reason = reason


REASONS = {'no-tools': 'secret-tool or busctl is not installed', 'no-runtime': 'there is no private runtime folder ($XDG_RUNTIME_DIR)',
           'no-service': 'no keyring (Secret Service) is running', 'locked': 'the keyring is locked',
           'missing': 'the keyring has no message key for this Machine', 'timeout': 'the keyring did not answer',
           'failed': 'the keyring could not be read', 'wrong-key': 'the keyring holds a different key than this Machine\'s'}


def _env():
    """The session bus also when the service started before the desktop session gave it one (systemd puts it there)."""
    env = dict(os.environ)
    run = env.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
    if not env.get('DBUS_SESSION_BUS_ADDRESS') and Path(run, 'bus').exists():
        env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path={run}/bus'
    env.setdefault('XDG_RUNTIME_DIR', run)
    for name in ('DISPLAY', 'WAYLAND_DISPLAY'):  # nothing this does may put a prompt on the screen
        env.pop(name, None)
    return env


class SecretService:
    """The Secret Service on the session bus, through secret-tool and busctl."""

    def ready(self):
        if not (shutil.which('secret-tool') and shutil.which('busctl')):
            raise Unavailable('no-tools')

    def _run(self, args, data=None):
        try:
            return subprocess.run(args, input=data, capture_output=True, env=_env(), timeout=TIMEOUT + 5)
        except subprocess.TimeoutExpired:
            raise Unavailable('timeout') from None
        except OSError:
            raise Unavailable('no-tools') from None

    def _bus(self, *args):
        run = self._run(['busctl', '--user', '--json=short', f'--timeout={TIMEOUT}', *args])
        if run.returncode:
            error = run.stderr.decode(errors='replace')
            if 'timed out' in error:
                raise Unavailable('timeout')
            if 'not provided by any' in error or 'ServiceUnknown' in error or 'No such file' in error \
                    or 'Connection refused' in error or 'not activatable' in error:
                raise Unavailable('no-service')
            raise Unavailable('failed', 'the keyring answered with an error')
        try:
            return json.loads(run.stdout)['data']
        except (ValueError, KeyError, TypeError):
            raise Unavailable('failed', 'the keyring gave an answer this can\'t read') from None

    def state(self, attributes):
        """'unlocked', 'locked' or 'missing' for the item with these attributes; never prompts."""
        pairs = [str(len(attributes))] + [part for pair in attributes.items() for part in pair]
        unlocked, locked = self._bus('call', SERVICE, '/org/freedesktop/secrets', 'org.freedesktop.Secret.Service',
                                     'SearchItems', 'a{ss}', *pairs)
        return 'unlocked' if unlocked else 'locked' if locked else 'missing'

    def collection(self):
        """(object path of the default collection, locked) or raises."""
        path = self._bus('call', SERVICE, '/org/freedesktop/secrets', 'org.freedesktop.Secret.Service', 'ReadAlias', 's', 'default')
        path = path[0] if isinstance(path, list) else path
        if not path or path == '/':
            raise Unavailable('no-service', 'the keyring has no default collection')
        locked = self._bus('get-property', SERVICE, path, 'org.freedesktop.Secret.Collection', 'Locked')
        return path, bool(locked)

    def read(self, attributes):
        state = self.state(attributes)
        if state != 'unlocked':
            raise Unavailable(state)
        run = self._run(['secret-tool', 'lookup', *[p for pair in attributes.items() for p in pair]])
        if run.returncode or not run.stdout.strip():
            raise Unavailable('failed')
        return run.stdout.strip()

    def store(self, attributes, value):
        _, locked = self.collection()
        if locked:  # storing would ask to unlock it
            raise Unavailable('locked')
        run = self._run(['secret-tool', 'store', f'--label={LABEL}', *[p for pair in attributes.items() for p in pair]], value)
        if run.returncode:
            raise Unavailable('failed', 'the keyring did not store the key')

    def clear(self, attributes):
        if self.state(attributes) == 'missing':
            return
        run = self._run(['secret-tool', 'clear', *[p for pair in attributes.items() for p in pair]])
        if run.returncode:
            raise Unavailable('failed', 'the keyring did not remove the item')


BACKEND = SecretService()


def attributes(ident, kind=KIND):
    return {'application': 'the-machine', 'kind': kind, 'id': ident}


def check_value(key):
    """A keyed hash of the key: tells this Machine's key from another one, and says nothing about it."""
    return hashlib.blake2b(b'', key=key, person=b'mc1-check', digest_size=16).hexdigest()


def marker_path(database):
    return message_seal.key_path(database).with_name(MARKER)


def marker(database):
    try:
        value = json.loads(marker_path(database).read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get('id'), str) and value['id'].isalnum() else None


def runtime_dir():
    """The user's private runtime folder (tmpfs, emptied at every reboot), or Unavailable."""
    path = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}')
    try:
        info = path.stat()
    except OSError:
        raise Unavailable('no-runtime') from None
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Unavailable('no-runtime', f'{path} is not private to this account')
    return path


def runtime_path(ident):
    return runtime_dir() / 'the-machine-key' / ident / message_seal.KEY_NAME


def _write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{secrets.token_hex(4)}.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    except BaseException:
        with_suppress(os.unlink, temp)
        raise
    _sync_dir(path.parent)


def with_suppress(func, *args):
    try:
        func(*args)
    except OSError:
        pass


def _sync_dir(folder):
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _decode(raw):
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError:
        return None
    return key if len(key) == 32 else None


def read(database, mark=None):
    """The key from the keyring, checked against the marker; raises Unavailable."""
    mark = mark or marker(database)
    if not mark:
        raise Unavailable('missing', 'this Machine\'s key is not in a keyring')
    BACKEND.ready()
    key = _decode(BACKEND.read(attributes(mark['id'])))
    if not key or not hmac.compare_digest(check_value(key), str(mark.get('check', ''))):
        raise Unavailable('wrong-key')
    return key


def fill(database, key, mark=None):
    """Put the key in the runtime file the data folder's link points at (for the other processes and the plugin)."""
    mark = mark or marker(database)
    target = runtime_path(mark['id'])
    link = message_seal.key_path(database)
    if link.is_symlink():  # where the data folder's link points, also when this process sees another runtime folder
        pointed = Path(os.readlink(link))
        if pointed.name == message_seal.KEY_NAME and pointed.parent.name == mark['id'] and pointed.parent.parent.name == 'the-machine-key':
            target = pointed
    for folder in (target.parent.parent, target.parent):
        folder.mkdir(mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
    _write_private(target, base64.b64encode(key) + b'\n')
    return target


def sealed_checks(database, key):
    """How many sealed things this key was checked to open now (values from each sealed column, the saved sign-ins); raises SealError when one does not open."""
    path, checked = Path(database).expanduser(), 0
    sealer = message_seal.Sealer(key)
    if path.is_file():
        with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)) as db:
            if message_seal.is_protected(db):
                for table, column, plain in message_seal.sealed_columns(db):
                    # names from message_seal's fixed lists (sealed_columns), never input; checked to be plain names
                    if not (re.fullmatch(r'[a-z_]+', table) and re.fullmatch(r'[a-z_]+', column)):
                        continue
                    kind = "typeof({c})='blob'" if plain is None else "typeof({c})='text' AND instr({c},'" + message_seal.SEALED_KEY + "')"
                    for (value,) in db.execute(f'SELECT {column} FROM {table} WHERE {kind.format(c=column)} LIMIT 3'):
                        if message_seal.is_sealed(value) or message_seal.is_sealed_json(value):
                            sealer.open_any(value)
                            checked += 1
    logins = path.parent / 'browser-logins.sealed'
    if message_seal.is_sealed_file(logins):
        sealer.open_bytes(logins.read_bytes())
        checked += 1
    return checked


def probe(round_trip=True):
    """Whether this computer's keyring can hold the key: the tools, a private runtime folder, an unlocked default
    collection, and (`round_trip`) a throwaway secret stored, read back and removed. Returns the collection's path;
    raises Unavailable."""
    BACKEND.ready()
    runtime_dir()
    collection, locked = BACKEND.collection()
    if locked:
        raise Unavailable('locked')
    if not round_trip:
        return collection
    test = attributes(secrets.token_hex(8), PROBE)
    value = base64.b64encode(secrets.token_bytes(32))
    try:
        BACKEND.store(test, value)
        if not hmac.compare_digest(BACKEND.read(test), value):
            raise Unavailable('failed', 'the keyring gave back something else than it was given')
    finally:
        BACKEND.clear(test)
    if BACKEND.state(test) != 'missing':
        raise Unavailable('failed', 'the keyring did not remove a test item')
    return collection


def unencrypted_keyring(collection):
    """A GNOME keyring without a password is kept on the disk unencrypted ("[keyring]" as text): the key then only
    leaves the data folder. Its file, or None (also when this can't be told)."""
    if not collection or not collection.startswith('/org/freedesktop/secrets/collection/'):
        return None
    # D-Bus escapes a name's other characters as _XX (Default_keyring is Default_5fkeyring)
    name = re.sub(r'_([0-9a-f]{2})', lambda m: chr(int(m.group(1), 16)), collection.rsplit('/', 1)[1])
    folder = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'keyrings'
    path = folder / f'{name}.keyring'
    try:
        with open(path, 'rb') as handle:
            return path if handle.read(9) == b'[keyring]' else None
    except OSError:
        return None


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def to_keyring(database, say=print):
    """Move the key from its file into the keyring. Each step is checked before the next; the file goes last, and
    is overwritten before it is removed. Run again after an interruption, it finishes the move."""
    database = Path(database).expanduser()
    key_file = message_seal.key_path(database)
    mark = marker(database)
    if mark and message_seal.held_elsewhere(database):
        settle(database)
        say('The message key is already in your keyring.')
        return False
    if message_seal.held_elsewhere(database):
        raise Unavailable('failed', 'this Machine\'s key is kept by its hosting, not in a file; nothing to move')
    key = message_seal.load_key(database)
    if not key or key_file.is_symlink():
        raise Unavailable('failed', f'there is no message key file at {key_file}')
    collection = probe()
    sealed_checks(database, key)  # the file's key opens what is sealed now, or nothing moves
    ident = mark['id'] if mark and mark.get('check') == check_value(key) else secrets.token_hex(12)
    item = attributes(ident)
    stored_here = False
    try:
        if BACKEND.state(item) == 'missing':
            BACKEND.store(item, base64.b64encode(key))
            stored_here = True
        mark = {'version': 1, 'store': 'secret-service', 'id': ident, 'collection': collection, 'check': check_value(key),
                'moved_at': _now()}
        if not hmac.compare_digest(read(database, mark), key):
            raise Unavailable('wrong-key')
        checked = sealed_checks(database, read(database, mark))  # what the keyring gives back opens the data
        target = fill(database, key, mark)
        if _decode(target.read_bytes().strip()) != key:
            raise Unavailable('failed', 'the key in memory did not read back')
        _write_private(marker_path(database), (json.dumps(mark, indent=2) + '\n').encode())
    except BaseException:
        if stored_here:
            with_suppress_unavailable(BACKEND.clear, item)
        raise
    # The switch: the file's name becomes the link to memory, in one rename; the old file stays reachable as MOVING
    # until the link checks out, then it is overwritten and removed.
    moving = key_file.with_name(MOVING)
    with_suppress(os.unlink, moving)
    os.link(key_file, moving)
    link = key_file.with_name(key_file.name + f'.{secrets.token_hex(4)}.link')
    os.symlink(target, link)
    os.replace(link, key_file)
    _sync_dir(key_file.parent)
    forget()
    if message_seal.load_key(database) != key:  # never expected: put the file back as it was
        os.replace(moving, key_file)
        _sync_dir(key_file.parent)
        raise Unavailable('failed', 'the key did not read back through the keyring; the file was put back')
    settle(database)
    say(f'The message key is in your keyring now (checked against {checked} sealed item{"s" if checked != 1 else ""}); '
        f'{key_file} is a link to memory that The Machine fills at each start.')
    plain = unencrypted_keyring(collection)
    if plain:
        say(f'Note: your keyring has no password, so {plain} keeps it unencrypted on this disk. The key is out of the '
            'data folder (copies and backups of it hold no key), but not encrypted at rest. A keyring password fixes that.')
    return True


def with_suppress_unavailable(func, *args):
    try:
        func(*args)
    except Unavailable:
        pass


def settle(database):
    """After a move to the keyring: the old file kept as MOVING goes once the keyring's key matches it."""
    moving = message_seal.key_path(database).with_name(MOVING)
    if not moving.is_file() or not message_seal.held_elsewhere(database):
        return False
    old = _decode(moving.read_bytes().strip())
    now = message_seal.load_key(database)
    if old and now and hmac.compare_digest(old, now):
        import portable
        portable.shred(moving)
        _sync_dir(moving.parent)
        return True
    return False


def to_file(database, say=print, keep_in_keyring=False, attempts=6, every=5):
    """Put the key back in `message-content.key` next to the database: the way back from to_keyring."""
    database = Path(database).expanduser()
    key_file = message_seal.key_path(database)
    mark = marker(database)
    if not mark:
        if key_file.is_file() and not key_file.is_symlink():
            say(f'The message key is already in {key_file}.')
            return False
        raise Unavailable('failed', 'this Machine\'s key is not in a keyring')
    key, error = None, None
    for attempt in range(attempts):  # the copy in memory first, then the keyring itself
        key = message_seal.load_key(database)
        if key:
            break
        try:
            key = read(database, mark)
            break
        except Unavailable as failed:
            error = failed
            if failed.reason in ('wrong-key', 'missing', 'no-tools') or attempt == attempts - 1:
                raise
            time.sleep(every)
    if not key:
        raise error or Unavailable('failed')
    if not hmac.compare_digest(check_value(key), str(mark.get('check', ''))):
        raise Unavailable('wrong-key')
    checked = sealed_checks(database, key)
    temp = key_file.with_name(key_file.name + f'.{secrets.token_hex(4)}.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(base64.b64encode(key) + b'\n')
        out.flush()
        os.fsync(out.fileno())
    if _decode(temp.read_bytes().strip()) != key:
        os.unlink(temp)
        raise Unavailable('failed', 'the key file did not read back')
    os.replace(temp, key_file)  # the link becomes the file, in one rename
    _sync_dir(key_file.parent)
    forget()
    if message_seal.load_key(database) != key:
        raise Unavailable('failed', f'{key_file} did not read back; the keyring still holds the key')
    marker_path(database).unlink()
    _sync_dir(key_file.parent)
    with_suppress(os.unlink, key_file.with_name(MOVING))
    try:
        import portable
        runtime = runtime_path(mark['id'])
        portable.shred(runtime)
        with_suppress(os.rmdir, runtime.parent)
    except Unavailable:
        pass
    if not keep_in_keyring:
        try:
            BACKEND.clear(attributes(mark['id']))
        except Unavailable as failed:
            say(f'The keyring still holds a copy ({failed}); remove it later with: secret-tool clear application the-machine kind {KIND} id {mark["id"]}')
    say(f'The message key is back in {key_file} (checked against {checked} sealed item{"s" if checked != 1 else ""}). '
        'Back it up with the database: without it the messages are lost.')
    return True


def status(database):
    """Where the key is kept and whether it can be read now, for `assistant message-key status`. Never the key."""
    database = Path(database).expanduser()
    key_file = message_seal.key_path(database)
    mark = marker(database)
    out = {'key': str(key_file), 'kept': 'keyring' if mark and key_file.is_symlink() else
           'hosting' if message_seal.held_elsewhere(database) else 'file' if key_file.is_file() else 'none'}
    if mark:
        out.update({'collection': mark.get('collection'), 'moved_at': mark.get('moved_at'), 'id': mark['id']})
        if not key_file.is_symlink():
            out['note'] = 'a move between the file and the keyring did not finish: run to-keyring or to-file again'
        try:
            read(database, mark)
            out['keyring'] = 'readable'
        except Unavailable as error:
            out['keyring'] = f'not readable now: {error}'
        out['in_memory'] = key_file.is_file()
        if key_file.with_name(MOVING).exists():
            out['note'] = f'the old key file is still at {key_file.with_name(MOVING)}: run to-keyring again to finish'
        plain = unencrypted_keyring(mark.get('collection'))
        if plain:
            out['warning'] = f'your keyring has no password: {plain} keeps it unencrypted on this disk'
    elif out['kept'] == 'file':
        try:
            out['keyring'] = 'looks ready (assistant message-key to-keyring tests it first)' if probe(round_trip=False) else ''
        except Unavailable as error:
            out['keyring'] = f'can\'t hold it here: {error}'
    return out


# ------------------------------------------------------------------ read once per process, kept in memory

_cache, _failed = {}, {}


def forget():
    _cache.clear()
    _failed.clear()


def cached(database, mark, force=False):
    """The key for a database whose key is in the keyring: from memory, else from the keyring (at most once every
    RETRY_AFTER seconds unless `force`), refilling the runtime file when it went. None when it can't be read now;
    `last_error(database)` says why."""
    ident = mark['id']
    key = _cache.get(ident)
    if not key:
        if not force and time.monotonic() - _failed.get(ident, (-1e9, None))[0] < RETRY_AFTER:
            return None
        try:
            key = read(database, mark)
        except Unavailable as error:
            _failed[ident] = (time.monotonic(), error)
            return None
        _cache[ident] = key
        _failed.pop(ident, None)
    try:
        target = runtime_path(ident)
        if not target.is_file():
            fill(database, key, mark)
    except (OSError, Unavailable):
        pass  # this process has the key; the next one tries the file again
    return key


def remember(mark, key):
    _cache[mark['id']] = key


def last_error(database):
    mark = marker(database)
    found = _failed.get(mark['id']) if mark else None
    return found[1] if found else None
