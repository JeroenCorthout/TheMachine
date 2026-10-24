"""Message contents sealed at rest in The Machine's database (docs/security.md, "Message contents at rest").

Every source writes its signals through one table, `events`: a subject or title, a body, and metadata. When
`message_protection` is on, those are stored sealed, whatever the source (mail, chat, notifications, transcripts,
calendar descriptions, and any connector added later):

- `title` and `body` become sealed blobs;
- `metadata` keeps in the clear only the keys the app looks up in SQL (PLAIN_METADATA: thread and chat ids, sender
  and recipients, labels); every other key, including any a new connector adds, is sealed with the rest;
- the keyword index (`event_search`) holds keyed hashes of the words instead of the words;
- text the app derives from messages (card text, drafts, edits, prepared tool results, role quotes,
  promises) is sealed too (DERIVED).

The app itself reads everything as before: its connections open sealed values as they are read (`attach`). A
connection without the key (sqlite3 on the command line, a support tool) sees only blobs, and its writes to these
tables fail rather than store plain text.

On by default, on every Machine: only `"message_protection": false` in config.json keeps (or makes) the database plain
(`wanted`). The key is 32 random bytes in `message-content.key` next to the database, readable only by the owner's account. It
protects against reading the database without that account: a copied database file, a support tool that opens the
file, someone signed in as another user. It does not protect against root on the same server, which can read the key
or the running app's memory; nor against what other programs keep (OpenClaw's session transcripts, a chat bridge's
own store). A phone notification waiting for the app (android_inbox) is sealed by the Gateway's plugin (INBOX). Backups and exports carry the key with the database, so they stay readable for the owner.

A hosting layer may keep the key off the disk instead (`held_elsewhere`): `message-content.key` is then a link to a file
in memory (under /run) that the host puts there at every start. Backups of the disk then don't hold the key, and a
missing key is waited for (`wait_for_key`), never made anew: a new key would leave the messages unreadable.

A self-hosted Machine on a Linux desktop can keep it in the desktop's keyring instead (message_keyring.py,
`assistant message-key to-keyring`): `message-content.keyring` next to the database says so, and the key file is then
a link to memory too, which the app fills from the keyring at each start (waiting while the keyring is locked or not
running yet) and keeps in memory once read.

Sealing: a fresh 16-byte nonce; the keystream is SHAKE256(key, nonce); a 16-byte BLAKE2b MAC over all of it
(encrypt-then-MAC); keys for sealing, MAC and the index are derived from the stored key with BLAKE2b. Standard
library only, as in network_crypto.py."""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import unicodedata
from contextlib import closing
from pathlib import Path

MAGIC = b'\x00\xffMC1\x00'
FILE_MAGIC = b'\x00\xffMF1\x00'  # a sealed file: an email attachment kept on disk (mail_attachments.py)
NONCE, TAG = 16, 16
KEY_NAME = 'message-content.key'
KEYRING_NAME = 'message-content.keyring'  # the key is in the desktop's keyring (message_keyring.py)
SEALED_KEY = 'mc1_sealed'
# Metadata keys the app queries in SQL (json_extract, instr): routing and identity, never what a message says
PLAIN_METADATA = frozenset({'thread_id', 'from', 'to', 'labels', 'notification_key_hash', 'user_id', 'from_me',
                            'channel_id', 'chat_id', 'conversation_id'})
# Text the app derives from messages: card text and the drafts behind it, edits, what a preparation's tools returned,
# a review's own summary (and its answer on a board check), what teammates' Machines asked and answered, what they shared in the team's Graph (and the review's
# line for each shared thread), a Claude Code or Codex job's brief and result, a task page's record (with its link's key), the weekly recap as sent and the lines it offers a choice on, the line a role or an organisation was read from, promises (what
# and the note closing one), and the messages a profile, roles or skills run was given. JSON columns keep the named
# keys in the clear. Not here, on purpose: columns the app matches in SQL, and what the Machine learned (facts, rules,
# feedback, skills, profile, voices, roles), which support may read (docs/configuration.md).
DERIVED = {'suggestions': ('title', 'body', 'handled_hint', 'owner_words', 'title_suggested'), 'feedback_prompts': ('title',),
           'person_roles': ('quote',), 'person_orgs': ('quote',), 'person_facts': ('value',), 'promises': ('what', 'note'), 'profile_runs': ('evidence',),
           'draft_edits': ('before_subject', 'before_body', 'after_subject', 'after_body'),
           'graph_unnamed': ('titles',), 'preparation_calls': ('params', 'result'), 'analyses': ('summary',),
           'fleet_in': ('question', 'draft'), 'fleet_out': ('question', 'answer'), 'network_replies': ('body',),
           'task_pages': ('record',), 'recaps': ('text',), 'recap_items': ('line', 'data'),
           'projects': ('stage_reason', 'next_step', 'sort_note', 'notes', 'outcome_reason'), 'board_fits': ('why',), 'board_backfill_asks': ('why',), 'project_moves': ('reason',), 'board_suggestions': ('about', 'reason'), 'board_offers': ('answer',),
           'team_mail': ('body',), 'team_records': ('body',), 'team_files': ('data',), 'team_blobs': ('dk',), 'share_lines': ('line',),
           'agent_jobs': ('brief', 'asks', 'progress', 'summary', 'diff', 'files', 'error', 'answers')}
DERIVED_JSON = (('action_jobs', 'payload', frozenset({'kind'})), ('suggestions', 'next_action', frozenset({'kind'})),
                ('suggestions', 'outcome', frozenset({'state'})))
# The phone's notifications reach the app through android_inbox, written by the Gateway's plugin
# (openclaw-plugin/android-feed.ts), which reads the key but has no BLAKE2b with a key in Node: their text is sealed
# with HMAC-SHA256 derived keys instead, the same SHAKE256 keystream and a 16-byte HMAC-SHA256 tag, as
# INBOX + base64(nonce, sealed, tag). Deleted once the app has read it.
INBOX = 'mci1:'
WORD = re.compile(r'[^\W_]+')
# The Machine's own signals (its Gateway's log, its own faults): logs, not anyone's message
SYSTEM_KINDS = ('gateway.', 'machine.')


class SealError(ValueError):
    pass


def wanted(config):
    """Message protection is on unless config.json turns it off explicitly with "message_protection": false."""
    return config.get('message_protection') is not False


def work_running(database, minutes=40):
    """Review, preparation and profile turns heard from in the last `minutes` (one quiet longer is stuck, not running),
    counted as the hosted updater counts them: sealing or opening the database waits until this is 0. None when the
    database can't be read: not proof that nothing runs."""
    import datetime
    path = Path(database).expanduser()
    if not path.is_file():
        return 0
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)).isoformat(timespec='seconds')
    active = 0
    try:
        with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10)) as db:
            present = _tables(db)
            for table, query in (('review_runs', "SELECT count(*) FROM review_runs WHERE status IN ('starting','running','recovering') AND updated_at>?"),
                                 ('preparations', "SELECT count(*) FROM preparations WHERE status IN ('starting','running') AND updated_at>?"),
                                 ('profile_runs', "SELECT count(*) FROM profile_runs WHERE status IN ('starting','running','recovering') AND updated_at>?")):
                if table in present:
                    active += db.execute(query, (since,)).fetchone()[0]
    except sqlite3.Error:
        return None
    return active


def problems(database):
    """What the owner has to fix about the key, as [(id, text, fix)] (assistant doctor and the dashboard's Needs you).
    Without the key the messages are lost for good, so it goes in every backup of the database."""
    path = Path(database).expanduser()
    sealed, has_key = state(path)
    key, out = key_path(path), []
    if in_keyring(path):
        out += _keyring_problems(path, sealed, has_key)
    elif sealed and not has_key and held_elsewhere(path):
        out.append(('message-key-waiting', 'Your messages are encrypted and their key is kept off this server\'s disk by your hosting. '
                    'It has not arrived yet, so the app waits for it.', 'it comes once this Machine reaches its hosting; check the connection'))
    elif sealed and not has_key:
        out.append(('message-key-missing', f'Your messages are encrypted, but their key {key} is missing. Without it they can\'t be read.',
                    f'put {KEY_NAME} back from your backup, next to {path.name}'))
    elif has_key and key.stat().st_mode & 0o077:
        out.append(('message-key-open', f'The message key {key} is readable by others.', f'chmod 600 {key}'))
    copy = path.with_name(path.name + '.before-protection')
    if copy.exists():
        out.append(('message-protection-failed', f'An unencrypted copy of your messages from an earlier version waits at {copy}.',
                    'assistant service logs; it is removed at the next start once the database checks out'))
    if path.with_name('.' + path.name + '.before-protection.d').exists():
        out.append(('message-protection-failed', 'Encrypting or opening your messages did not finish; an encrypted copy of the database before it is kept.',
                    'assistant service logs; it is put back or removed at the next start'))
    return out


def _keyring_problems(path, sealed, has_key):
    import message_keyring
    key, out = key_path(path), []
    mark = message_keyring.marker(path)
    if not key.is_symlink():
        if key.is_file():
            out.append(('message-key-keyring-unfinished', f'Moving the message key between {key} and your keyring did not finish. '
                        'The file still holds it, so nothing is lost.', 'assistant message-key to-keyring (or to-file)'))
        elif sealed:
            out.append(('message-key-missing', f'Your messages are encrypted and their key is in your keyring, but {key} is gone.',
                        'assistant message-key to-file'))
    elif not has_key and not (mark and message_keyring.cached(path, mark)):
        error = message_keyring.last_error(path)
        out.append(('message-key-keyring-waiting', 'Your messages are encrypted and their key is in your keyring, which can\'t be read '
                    f'right now ({error or "not answering"}). The app waits for it and starts by itself once it can.',
                    'unlock your keyring or sign in to the desktop; details: assistant message-key status'))
    if key.with_name(message_keyring.MOVING).exists():
        out.append(('message-key-leftover', f'A copy of the message key from the move to your keyring is still in {key.with_name(message_keyring.MOVING)}.',
                    'assistant message-key to-keyring (it finishes the move)'))
    return out


def in_keyring(database):
    """The key is kept in the desktop's keyring (message_keyring.py); the key file is then a link to memory."""
    return key_path(database).with_name(KEYRING_NAME).exists()


def state(database):
    """(sealed, key present) for the database at this path, read without the key; (None, …) when there is no database."""
    path = Path(database).expanduser()
    key = key_path(path).is_file()
    if not path.is_file():
        return None, key
    try:
        with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10)) as db:
            return is_protected(db), key
    except sqlite3.Error:
        return None, key


def key_path(database):
    return Path(database).expanduser().parent / KEY_NAME


def held_elsewhere(database):
    """The key is kept off the disk by a hosting layer: the key file is a link to where the host puts it at each start."""
    return key_path(database).is_symlink()


def load_key(database, create=False):
    path = key_path(database)
    keyring = None
    if path.with_name(KEYRING_NAME).exists():  # even one that can't be read means: never a new key here
        import message_keyring
        keyring = message_keyring.marker(database) or {}
    try:
        raw = path.read_bytes().strip()
    except FileNotFoundError:
        if keyring is not None:  # in the keyring: what this process read before, else the keyring itself
            key = message_keyring.cached(database, keyring) if keyring else None
            if key or not create:
                return key
            raise SealError(f'{path} is kept in your keyring, which can\'t be read right now') from None
        if not create:
            return None
        if path.is_symlink():  # the host's key has not arrived: never a new one here, it would orphan what it sealed
            raise SealError(f'{path} is kept by the hosting and has not arrived yet') from None
        raw = base64.b64encode(secrets.token_bytes(32))
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(raw + b'\n')
            out.flush()
            os.fsync(out.fileno())
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise SealError(f'{path} is not a message key')
    if keyring and path.is_symlink():  # the copy in memory the app filled from the keyring: only this Machine's key
        if not hmac.compare_digest(message_keyring.check_value(key), str(keyring.get('check', ''))):
            raise SealError(f'{path} does not hold this Machine\'s message key')
        message_keyring.remember(keyring, key)
    return key


def wait_for_key(database, say=print, every=5, limit=None, protect=False, waiting=None):
    """At the service's start: a database whose key the host keeps (`held_elsewhere`) or the desktop's keyring keeps
    (`in_keyring`) waits until the key is there when it is sealed (or is to be, `protect`), saying so once a minute,
    rather than starting without it: never a new key, which would orphan what is sealed. A key in the keyring is read
    from it at each try (a locked keyring is never asked to unlock); `waiting(reason)` hears why while it waits, and
    `waiting(None)` once it is over. True when the key is there (or not needed)."""
    path, waited, told = Path(database).expanduser(), 0, False
    try:
        while held_elsewhere(path) and not key_path(path).is_file() and (protect or state(path)[0]):
            reason = None
            if in_keyring(path):
                import message_keyring
                mark = message_keyring.marker(path)
                if mark and message_keyring.cached(path, mark, force=True):
                    return True  # read, kept in memory and put in the runtime file
                reason = str(message_keyring.last_error(path) or 'its marker can\'t be read')
                if waiting:
                    waiting(reason)
                    told = True
            if limit is not None and waited >= limit:
                return False
            if waited % 60 == 0:
                say(f'Waiting for the message key from your keyring ({reason}); the messages stay encrypted until it can be read. '
                    'Unlock the keyring or sign in to the desktop; details: assistant message-key status' if reason else
                    f'Waiting for the message key from the hosting ({key_path(path)}); the messages stay encrypted until it comes')
            time.sleep(every)
            waited += every
        return True
    finally:
        if told:
            waiting(None)


class Sealer:
    def __init__(self, key):
        derive = lambda name: hashlib.blake2b(key=key, person=name, digest_size=32).digest()
        self._enc, self._mac, self._index = derive(b'mc1-seal'), derive(b'mc1-mac'), derive(b'mc1-index')
        inbox = lambda name: hmac.new(key, name, hashlib.sha256).digest()  # what Node's crypto can do too (INBOX)
        self._inbox_enc, self._inbox_mac = inbox(b'mc1-inbox-seal'), inbox(b'mc1-inbox-mac')
        self._word = functools.lru_cache(maxsize=100_000)(self._word)  # a mailbox's words repeat a lot

    def _stream(self, nonce, size):
        return hashlib.shake_256(self._enc + nonce).digest(size)

    def _tag(self, nonce, sealed):
        return hashlib.blake2b(MAGIC + nonce + sealed, key=self._mac, digest_size=TAG).digest()

    def seal(self, value):
        """A str becomes a sealed blob; anything else (None, numbers, blobs already sealed, '') stays as it is."""
        if not isinstance(value, str) or not value:
            return value
        data = value.encode('utf-8')
        nonce = os.urandom(NONCE)
        sealed = (int.from_bytes(data, 'little') ^ int.from_bytes(self._stream(nonce, len(data)), 'little')).to_bytes(len(data), 'little')
        return MAGIC + nonce + sealed + self._tag(nonce, sealed)

    def open(self, value):
        if not is_sealed(value):
            return value
        nonce, sealed, tag = value[len(MAGIC):len(MAGIC) + NONCE], value[len(MAGIC) + NONCE:-TAG], value[-TAG:]
        if len(nonce) != NONCE or not hmac.compare_digest(tag, self._tag(nonce, sealed)):
            raise SealError('A sealed message did not open: wrong key or a damaged database')
        data = (int.from_bytes(sealed, 'little') ^ int.from_bytes(self._stream(nonce, len(sealed)), 'little')).to_bytes(len(sealed), 'little')
        return data.decode('utf-8')

    def seal_bytes(self, data):
        """A file's bytes sealed as a whole (FILE_MAGIC): an email attachment at rest. Same key and keystream as
        `seal`, its own magic in the tag, so a sealed file never opens as a value and the other way round."""
        nonce = os.urandom(NONCE)
        sealed = (int.from_bytes(data, 'little') ^ int.from_bytes(self._stream(nonce, len(data)), 'little')).to_bytes(len(data), 'little')
        return FILE_MAGIC + nonce + sealed + self._file_tag(nonce, sealed)

    def _file_tag(self, nonce, sealed):
        return hashlib.blake2b(FILE_MAGIC + nonce + sealed, key=self._mac, digest_size=TAG).digest()

    def open_bytes(self, data):
        """A sealed file's bytes opened; plain bytes pass as they are."""
        if not is_sealed_bytes(data):
            return data
        nonce, sealed, tag = data[len(FILE_MAGIC):len(FILE_MAGIC) + NONCE], data[len(FILE_MAGIC) + NONCE:-TAG], data[-TAG:]
        if len(nonce) != NONCE or len(tag) != TAG or not hmac.compare_digest(tag, self._file_tag(nonce, sealed)):
            raise SealError('A sealed attachment did not open: wrong key or a damaged file')
        return (int.from_bytes(sealed, 'little') ^ int.from_bytes(self._stream(nonce, len(sealed)), 'little')).to_bytes(len(sealed), 'little')

    def seal_inbox(self, text):
        """Text the Gateway's plugin writes for the app to read (INBOX), as the plugin seals it; for tests."""
        if not isinstance(text, str) or not text:
            return text
        data, nonce = text.encode('utf-8'), os.urandom(NONCE)
        sealed = bytes(a ^ b for a, b in zip(data, hashlib.shake_256(self._inbox_enc + nonce).digest(len(data))))
        return INBOX + base64.b64encode(nonce + sealed + self._inbox_tag(nonce, sealed)).decode()

    def _inbox_tag(self, nonce, sealed):
        return hmac.new(self._inbox_mac, INBOX.encode() + nonce + sealed, hashlib.sha256).digest()[:TAG]

    def open_inbox(self, value):
        if not is_sealed_inbox(value):
            return value
        raw = base64.b64decode(value[len(INBOX):])
        nonce, sealed, tag = raw[:NONCE], raw[NONCE:-TAG], raw[-TAG:]
        if len(nonce) != NONCE or len(tag) != TAG or not hmac.compare_digest(tag, self._inbox_tag(nonce, sealed)):
            raise SealError('A sealed notification did not open: wrong key or damaged')
        return bytes(a ^ b for a, b in zip(sealed, hashlib.shake_256(self._inbox_enc + nonce).digest(len(sealed)))).decode('utf-8')

    def seal_json(self, text, plain=PLAIN_METADATA):
        """A JSON object keeps `plain` keys in the clear; the rest goes into one sealed member."""
        if not isinstance(text, str) or not text or is_sealed_json(text):
            return text
        try:
            value = json.loads(text)
        except ValueError:
            value = None
        if not isinstance(value, dict):
            return self.seal(text)
        hidden = {k: v for k, v in value.items() if k not in plain}
        if not hidden:
            return text
        kept = {k: v for k, v in value.items() if k in plain}
        kept[SEALED_KEY] = base64.b64encode(self.seal(json.dumps(hidden, ensure_ascii=False))).decode()
        return json.dumps(kept, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def open_json(self, text):
        if not is_sealed_json(text):
            return text
        value = json.loads(text)
        hidden = json.loads(self.open(base64.b64decode(value.pop(SEALED_KEY))))
        return json.dumps({**value, **hidden}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def open_any(self, value):
        if type(value) is bytes:
            return self.open(value)
        if type(value) is str:
            return self.open_json(value)
        return value

    def words(self, text):
        """The keyword index's words: as SQLite's unicode61 splits and folds them, then keyed-hashed."""
        text = self.open_any(text)
        if not isinstance(text, str):
            return text
        folded = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).casefold()
        return ' '.join(self._word(w) for w in WORD.findall(folded))

    def _word(self, word):
        return base64.b32encode(hashlib.blake2b(word.encode(), key=self._index, digest_size=8).digest()).decode().rstrip('=').lower()


def is_sealed(value):
    return type(value) is bytes and value.startswith(MAGIC)


def is_sealed_bytes(data):
    return type(data) is bytes and data.startswith(FILE_MAGIC) and len(data) >= len(FILE_MAGIC) + NONCE + TAG


def is_sealed_file(path):
    try:
        with open(path, 'rb') as handle:
            return handle.read(len(FILE_MAGIC)) == FILE_MAGIC
    except OSError:
        return False


def is_sealed_inbox(value):
    return type(value) is str and value.startswith(INBOX)


def open_inbox(sealer, value):
    """Opens text the Gateway's plugin sealed (INBOX); plain text passes. Sealed text without the key is an error."""
    if is_sealed_inbox(value) and not sealer:
        raise SealError(f'A sealed notification waits but {KEY_NAME} is missing')
    return sealer.open_inbox(value) if sealer else value


def is_sealed_json(value):
    if type(value) is not str or not value.lstrip().startswith('{') or SEALED_KEY not in value:
        return False
    try:
        marker = json.loads(value).get(SEALED_KEY)
        raw = base64.b64decode(marker, validate=True) if isinstance(marker, str) else b''
    except (ValueError, AttributeError):
        return False
    return is_sealed(raw)


def match(db, words, joiner):
    # `db`: a connection or the Store; either carries the sealer when the database is sealed
    """An FTS MATCH expression for these search words, each a phrase; hashed when the database is sealed."""
    sealer = getattr(db, 'sealer', None) if getattr(db, 'protected', False) else None
    phrases = []
    for word in words:
        if sealer:
            hashed = sealer.words(word)
            if hashed:
                phrases.append('"' + hashed + '"')
        else:
            phrases.append('"' + word.replace('"', '""') + '"')
    return joiner.join(phrases)


def plain_leaks(event, state=None, run=4):
    """Message text that would be stored in the clear: a run of `run` words of the event's title or body found in what
    stays readable (its source id and kind, plain metadata other than sender and recipients, the source's cursor).
    The connector tests run with MESSAGE_SEAL_AUDIT=1, which makes commit_page refuse such an event."""
    def words(value):
        return WORD.findall(json.dumps(value, ensure_ascii=False).casefold() if not isinstance(value, str) else value.casefold())
    text = words(event.get('title') or '') + ['\0'] + words(event.get('body') or '')
    runs = {tuple(text[i:i + run]) for i in range(len(text) - run + 1) if '\0' not in text[i:i + run]}
    if not runs:
        return []
    plain = {'source_id': event.get('source_id'), 'kind': event.get('kind'), 'cursor': state,
             **{f'metadata.{k}': v for k, v in (event.get('metadata') or {}).items() if k in PLAIN_METADATA - {'from', 'to'}}}
    if str(event.get('kind') or '').startswith(SYSTEM_KINDS):
        return []
    found = []
    for name, value in plain.items():
        seen = words(value) if value is not None else []
        if any(tuple(seen[i:i + run]) in runs for i in range(len(seen) - run + 1)):
            found.append(name)
    return found


class Connection(sqlite3.Connection):
    sealer, protected = None, False


def attach(db, sealer):
    """Let this connection read and write sealed values: functions for the triggers, and rows that open themselves."""
    db.sealer = sealer
    if not sealer:
        return db
    db.create_function('mc_seal', 1, sealer.seal)
    db.create_function('mc_is_sealed_json', 1, is_sealed_json, deterministic=True)
    db.create_function('mc_seal_json', 2, lambda text, column: sealer.seal_json(text, plain_keys(column)))
    db.create_function('mc_words', 1, sealer.words, deterministic=True)
    opened = sealer.open_any

    def row(cursor, values):
        if any(type(v) is bytes or (type(v) is str and v[:1] == '{') for v in values):
            values = tuple(opened(v) for v in values)
        return sqlite3.Row(cursor, values)
    db.row_factory = row
    return db


def plain_keys(column):
    """`column` is 'table.column'."""
    if column == 'events.metadata':
        return PLAIN_METADATA
    return next((plain for t, c, plain in DERIVED_JSON if f'{t}.{c}' == column), frozenset())


# ------------------------------------------------------------------ the database's state

SEARCH_PLAIN = {
    'search_insert': "CREATE TRIGGER search_insert AFTER INSERT ON events BEGIN\n"
                     "  INSERT INTO event_search(rowid,title,body) VALUES(new.id,new.title,new.body); END",
    'search_update': "CREATE TRIGGER search_update AFTER UPDATE OF title,body ON events BEGIN\n"
                     "  INSERT INTO event_search(event_search,rowid,title,body) VALUES('delete',old.id,old.title,old.body);\n"
                     "  INSERT INTO event_search(rowid,title,body) VALUES(new.id,new.title,new.body); END",
    'search_delete': "CREATE TRIGGER search_delete AFTER DELETE ON events BEGIN\n"
                     "  INSERT INTO event_search(event_search,rowid,title,body) VALUES('delete',old.id,old.title,old.body); END"}
SEARCH_TABLE_PLAIN = "CREATE VIRTUAL TABLE event_search USING fts5(title,body,content='events',content_rowid='id')"
SEARCH_TABLE_SEALED = "CREATE VIRTUAL TABLE event_search USING fts5(title,body,content='')"
SEARCH_SEALED = {name: sql.replace('new.title', 'mc_words(new.title)').replace('new.body', 'mc_words(new.body)')
                 .replace('old.title', 'mc_words(old.title)').replace('old.body', 'mc_words(old.body)')
                 for name, sql in SEARCH_PLAIN.items()}


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def sealed_columns(db):
    """[(table, column, json plain keys or None)] present in this database."""
    tables, out = _tables(db), []
    if 'events' in tables:
        out += [('events', 'title', None), ('events', 'body', None), ('events', 'metadata', PLAIN_METADATA)]
    for table, columns in DERIVED.items():
        if table in tables:
            present = _columns(db, table)
            out += [(table, c, None) for c in columns if c in present]
    for table, column, plain in DERIVED_JSON:
        if table in tables and column in _columns(db, table):
            out.append((table, column, plain))
    return out


# Table and column names in this module's SQL are never input: they come from the fixed events columns, DERIVED and DERIVED_JSON
# lists (sealed_columns), kept only where the database really has them; values are always bound parameters.
def _guard_sql(table, column, plain):
    """Triggers that seal whatever reaches the table in the clear, from any code path."""
    value = f"mc_seal_json({column},'{table}.{column}')" if plain is not None else f'mc_seal({column})'
    when_new = (f"typeof(new.{column})='text' AND new.{column} IS NOT mc_seal_json(new.{column},'{table}.{column}')" if plain is not None
                else f"typeof(new.{column})='text' AND new.{column}!=''")
    return [f"CREATE TRIGGER mc_{table}_{column}_insert AFTER INSERT ON {table} WHEN {when_new} BEGIN "
            f"UPDATE {table} SET {column}={value} WHERE rowid=new.rowid; END",
            f"CREATE TRIGGER mc_{table}_{column}_update AFTER UPDATE OF {column} ON {table} WHEN {when_new} BEGIN "
            f"UPDATE {table} SET {column}={value} WHERE rowid=new.rowid; END"]


def triggers(db):
    return {r[0]: r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")}


def is_protected(db):
    return any(name.startswith('mc_') for name in triggers(db))


def _set_search(db, sealed):
    if 'event_search' not in _tables(db):
        return
    wanted = SEARCH_SEALED if sealed else SEARCH_PLAIN
    for name in wanted:
        db.execute(f'DROP TRIGGER IF EXISTS {name}')
    # Made anew from the table: the old index's words (in the clear, or hashed) go with it. Sealed, the index keeps no
    # link to the table's text (contentless), so nothing in SQLite reads the sealed columns through it.
    db.execute('DROP TABLE event_search')
    db.execute(SEARCH_TABLE_SEALED if sealed else SEARCH_TABLE_PLAIN)
    for sql in wanted.values():
        db.execute(sql)
    title, body = ('mc_words(title)', 'mc_words(body)') if sealed else ('title', 'body')
    db.execute(f'INSERT INTO event_search(rowid,title,body) SELECT id,{title},{body} FROM events')
    db.execute("INSERT INTO event_search(event_search) VALUES('optimize')")


def ensure_triggers(db):
    """A sealed database keeps its guards and hashed index triggers, also after a migration remade a table."""
    present = triggers(db)
    for table, column, plain in sealed_columns(db):
        for sql in _guard_sql(table, column, plain):
            name = sql.split()[2]
            if name not in present:
                db.execute(sql)
    for name, sql in SEARCH_SEALED.items():
        if 'event_search' in _tables(db) and present.get(name) != sql:
            db.execute(f'DROP TRIGGER IF EXISTS {name}')
            db.execute(sql)


def protect(db):
    """Seal every message in place. Run inside one transaction, on a connection `attach`ed to the key."""
    sealer = db.sealer
    counts = {}
    for name in SEARCH_PLAIN:  # the index is made anew below, once
        db.execute(f'DROP TRIGGER IF EXISTS {name}')
    for table, column, plain in sealed_columns(db):
        rows = db.execute(f'SELECT rowid,{column} FROM {table}').fetchall()  # opened by the row factory
        changed = 0
        for rowid, value in rows:
            new = sealer.seal_json(value, plain) if plain is not None else sealer.seal(value)
            if new is not value and new != value:
                db.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (new, rowid))
                changed += 1
        counts[f'{table}.{column}'] = changed
    ensure_triggers(db)
    _set_search(db, True)
    return counts


def unprotect(db):
    """Open every sealed value in place and return to plain text and a plain index (rolling back, or an export)."""
    for name in list(triggers(db)):
        if name.startswith('mc_') or name in SEARCH_PLAIN:
            db.execute(f'DROP TRIGGER {name}')
    for table, column, plain in sealed_columns(db):
        for rowid, value in db.execute(f'SELECT rowid,{column} FROM {table}').fetchall():
            if is_sealed(value) or is_sealed_json(value):
                raise SealError('A value stayed sealed: this connection has no key')
            db.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (value, rowid))
    _set_search(db, False)


def leftovers(db, seal=False):
    """How many values a sealed database still holds in the clear (0 when protection is complete). With `seal`, seals
    them: a table made after the database was sealed (joining a Fleet, say) is covered from then on."""
    sealer, found = db.sealer, 0
    for table, column, plain in sealed_columns(db):
        rows = db.execute(f"SELECT rowid,{column} FROM {table} WHERE typeof({column})='text' AND {column}!=''"
                          + (f" AND mc_is_sealed_json({column})=0" if plain is not None else '')).fetchall()
        for rowid, value in rows:
            new = sealer.seal_json(value, plain) if plain is not None else sealer.seal(value)
            if new != value:
                found += 1
                if seal:
                    db.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (new, rowid))
    return found
