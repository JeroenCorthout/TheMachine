"""This Machine on The Machine Network: end-to-end sealing, an outbox, and delivery state (docs/network.md).

`Sealing` wraps the relay connection (fleet.Relay) the Fleet and Friends already use, so every message type goes
through it without each one knowing:

- The directory: each teammate's and friend's public keys are checked and pinned the first time they're seen; when a
  Machine's keys change later, the owner is warned (key_changes) and the new keys are pinned. This Machine publishes
  its own public keys when the directory doesn't show them yet.
- Asking: the question is sealed for each recipient's Machine (network_crypto), bound to who asks whom, what kind and
  the request id. Every message is sealed: a teammate's or friend's Machine without keys can't be asked until it
  updates, and nothing is ever sent in the clear (a relay can't strip keys to read along).
- Answering: an answer to a sealed question is sealed for the asking Machine, bound to the question's id.
- Receiving: sealed questions and answers are opened and checked; anything that doesn't open, and anything that
  isn't sealed, is dropped and logged, never shown.
- Outbox: an answer the relay didn't take because it was away (restarting, network down) is kept here and sent again
  with backoff, so a question is never acted on twice: this Machine remembers what it answered (network_taken) and
  sends that again if the same question comes back.

Private keys stay on this Machine, in network-keys.json (mode 600) next to the database."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import network_crypto as nc

LOG = logging.getLogger('personal-assistant')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS network_outbox(key TEXT PRIMARY KEY,method TEXT NOT NULL,path TEXT NOT NULL,body TEXT NOT NULL,
  tries INTEGER NOT NULL DEFAULT 0,next_at TEXT NOT NULL,created_at TEXT NOT NULL,error TEXT);
CREATE TABLE IF NOT EXISTS network_taken(ask_id TEXT PRIMARY KEY,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS network_twins(request_key TEXT PRIMARY KEY,ask_id TEXT NOT NULL,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS network_aliases(ask_id TEXT PRIMARY KEY,original_id TEXT NOT NULL,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS network_replies(ask_id TEXT PRIMARY KEY,body TEXT NOT NULL,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS network_pins(member_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,keys TEXT NOT NULL,name TEXT,
  first_at TEXT NOT NULL,changed_at TEXT);
CREATE TABLE IF NOT EXISTS network_sealed(ask_id TEXT PRIMARY KEY,kind TEXT NOT NULL,asker TEXT NOT NULL,me TEXT NOT NULL,at TEXT NOT NULL);
'''
OUTBOX_DAYS = 3          # an answer the relay can't take for this long is given up (the question expired there too)
KEEP_DAYS = 30


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def _later(seconds):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec='seconds')


def ensure(db):
    """The tables in their own shape: one of the same name in another shape (from before, or another feature's) is
    moved aside as <name>_old, never dropped."""
    for statement in SCHEMA.split(';'):
        if 'CREATE TABLE' not in statement:
            continue
        name = statement.split('EXISTS', 1)[1].split('(', 1)[0].strip()
        wanted = {c.strip().split()[0] for c in statement.split('(', 1)[1].rsplit(')', 1)[0].split(',') if c.strip() and not c.strip()[0].isdigit()}
        found = {r[1] for r in db.execute(f'PRAGMA table_info({name})')}
        if found and not wanted <= found:
            old, number = name + '_old', 2
            while db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (old,)).fetchone():
                old, number = name + '_old_' + str(number), number + 1
            db.execute(f'ALTER TABLE {name} RENAME TO {old}')
    for statement in SCHEMA.split(';'):
        if statement.strip():
            db.execute(statement)


def keys_path(config):
    """Where this Machine's private keys live: next to its database. None (no sealing) without a database path."""
    found = config.get('network_keys') or (Path(config['database']).expanduser().parent / 'network-keys.json' if config.get('database') else None)
    return Path(found).expanduser() if found else None


class Sealing:
    """The relay connection, sealing and opening on the way (see the module docstring)."""

    def __init__(self, inner, fleet, private=None):
        self.inner, self.fleet = inner, fleet
        self.private = private
        self.public = nc.public_keys(private) if private else None
        self.url = getattr(inner, 'url', '')
        with fleet.connect() as db:
            ensure(db)

    # -- what the Fleet calls
    def call(self, method, path, body=None, timeout=40):
        route = path.split('?', 1)[0]
        if not self.private and route in ('/ask', '/answer') and isinstance(body, dict) and (body.get('question') is not None or body.get('answer') is not None):
            raise ValueError("This Machine has no keys for The Machine Network, so it can't send anything there; nothing was sent.")
        if route == '/directory':
            return self._directory(self.inner.call(method, path, body, timeout=timeout))
        if route == '/ask' and method == 'POST' and isinstance(body, dict) and body.get('question') is not None:
            body = self._seal_ask(body)
            return self.inner.call(method, path, body, timeout=timeout)
        if route == '/answer' and method == 'POST':
            return self._answer(body, timeout)
        if route == '/withdraw' and method == 'POST':
            return self._withdraw(body, timeout)
        found = self.inner.call(method, path, body, timeout=timeout)
        if route == '/inbox':
            self._open_inbox(found)
        return found

    # -- the directory and keys
    def _directory(self, found):
        changes, publish = [], False
        with self.fleet.connect() as db:
            for group in (found or {}).get('fleets', []):
                for m in group.get('members', []):
                    if m.get('me'):
                        publish = publish or bool(self.public and (m.get('keys') or {}).get('box') != self.public['box'])
                        continue
                    checked = nc.check_keys(m.get('keys')) if m.get('keys') else None
                    if m.get('keys') and not checked:
                        LOG.warning('network keys do not check out member=%s', m.get('member_id'))
                        m['keys'] = None
                    if not checked:
                        continue
                    pin = db.execute('SELECT * FROM network_pins WHERE member_id=?', (m['member_id'],)).fetchone()
                    if not pin:
                        db.execute('INSERT INTO network_pins(member_id,fingerprint,keys,name,first_at) VALUES(?,?,?,?,?)',
                                   (m['member_id'], checked['fingerprint'], json.dumps(m['keys']), str(m.get('name') or '')[:80], now()))
                    elif pin['fingerprint'] != checked['fingerprint']:
                        db.execute('UPDATE network_pins SET fingerprint=?,keys=?,changed_at=? WHERE member_id=?',
                                   (checked['fingerprint'], json.dumps(m['keys']), now(), m['member_id']))
                        changes.append({'member_id': m['member_id'], 'name': str(m.get('name') or '')[:80], 'at': now(),
                                        'was': pin['fingerprint'], 'now': checked['fingerprint']})
        if publish:
            self._publish()
        if changes:
            seen = self.fleet.meta('network_key_changes', [])
            self.fleet.set_meta('network_key_changes', (changes + seen)[:20])
            for change in changes:
                LOG.warning('network keys changed member=%s', change['member_id'])
        return found

    def _publish(self):
        try:
            self.inner.call('POST', '/keys', self.public, timeout=30)
            if not self.fleet.meta('network_published_at'):
                self.fleet.set_meta('network_published_at', now())
        except Exception as error:  # away: published on a later directory round, never "off"
            LOG.info('network keys not published yet error=%s', type(error).__name__)

    def _group(self, ref):
        return (self.fleet.fleets().get(self.fleet.ref(ref)) or {})

    def _me(self, group):
        return next((m['member_id'] for m in group.get('members', []) if m.get('me')), None)

    def _keys_of(self, member_id):
        with self.fleet.connect() as db:
            pin = db.execute('SELECT keys FROM network_pins WHERE member_id=?', (member_id,)).fetchone()
        return json.loads(pin['keys']) if pin else None

    def _current_keys(self, group, member_id):
        """A member's keys as the directory shows them now, if they check out; else None."""
        m = next((x for x in group.get('members', []) if x.get('member_id') == member_id), None)
        return m.get('keys') if m and m.get('keys') and nc.check_keys(m['keys']) else None

    # -- asking
    def _seal_ask(self, body):
        """The question sealed for each recipient's Machine, and only that: never the question itself. Asking everyone
        skips a member whose Machine has no keys (none yet, or from before); asking someone by name whose Machine has
        none is refused, as is anyone whose keys disappeared since they were pinned."""
        group = self._group(body.get('fleet'))
        me = self._me(group)
        if not me:
            raise ValueError('This Machine is not in that group on The Machine Network; nothing was sent.')
        kind, question = body.get('kind'), body['question']
        named = bool(body.get('to')) or kind == 'followup'
        if body.get('to'):
            to = list(body['to'])
        elif kind == 'followup':
            to = sorted({i.get('member_id') for i in question.get('items', []) if i.get('member_id') and i.get('member_id') != me})
        else:
            to = [m['member_id'] for m in group.get('members', []) if not m.get('me')]
        sealed = {}
        for member_id in to:
            keys = self._current_keys(group, member_id)
            if keys:
                sealed[member_id] = nc.seal(question, self.private, keys, {'p': 'ask', 'k': kind, 'f': me, 't': member_id, 'r': body.get('request_id')})
            elif self._keys_of(member_id):
                raise ValueError("A teammate's Machine stopped showing its keys on The Machine Network; nothing was sent. Try again later.")
            elif named:
                raise ValueError("Their Machine needs an update before it can get questions on The Machine Network; nothing was sent.")
        if not sealed:
            raise ValueError("Nobody to ask yet: their Machines need an update before they can get questions on The Machine Network.")
        out = {**body, 'sealed': sealed, 'to': sorted(sealed)}
        out.pop('question')
        return out

    # -- answering, with the outbox
    def _answer(self, body, timeout):
        body = dict(body or {})
        ask_id = str(body.get('id') or '')
        with self.fleet.connect() as db:
            previous = db.execute('SELECT body FROM network_replies WHERE ask_id=?', (ask_id,)).fetchone()
            if previous and body.get('status') == 'waiting' and json.loads(previous[0]).get('status') != 'waiting':
                body = json.loads(previous[0])  # an old waiting acknowledgment cannot undo the final result
            db.execute('INSERT INTO network_replies(ask_id,body,at) VALUES(?,?,?) '
                       'ON CONFLICT(ask_id) DO UPDATE SET body=excluded.body,at=excluded.at', (ask_id, json.dumps(body), now()))
            aliases = [r[0] for r in db.execute('SELECT ask_id FROM network_aliases WHERE original_id=?', (ask_id,))]
        result = self._answer_one(body, timeout)
        for alias in aliases:
            self._answer_one({**body, 'id': alias}, timeout)
        return result

    def alias(self, ask_id, request_key):
        """Another node delivered the same request: keep one owner choice and answer both deliveries."""
        with self.fleet.connect() as db:
            original = db.execute('SELECT ask_id FROM network_twins WHERE request_key=?', (request_key,)).fetchone()
            if not original:
                return  # older taken records had no original-id mapping
            original_id = original[0]
            db.execute('INSERT OR IGNORE INTO network_aliases(ask_id,original_id,at) VALUES(?,?,?)', (ask_id, original_id, now()))
            saved = db.execute('SELECT body FROM network_replies WHERE ask_id=?', (original_id,)).fetchone()
        if saved:
            self._answer_one({**json.loads(saved[0]), 'id': ask_id}, 30)

    def _answer_one(self, body, timeout):
        body = dict(body or {})
        ask_id = str(body.get('id') or '')
        with self.fleet.connect() as db:
            row = db.execute('SELECT * FROM network_sealed WHERE ask_id=?', (ask_id,)).fetchone()
        if body.get('answer') is not None:  # sealed for the asking Machine, or not sent at all
            keys = self._keys_of(row['asker']) if row else None
            if not keys:
                raise ValueError("That question's Machine has no keys on The Machine Network, so the answer can't be sealed; nothing was sent.")
            body['sealed'] = nc.seal(body.pop('answer'), self.private, keys,
                                     {'p': 'answer', 'k': row['kind'], 'f': row['me'], 't': row['asker'], 'r': ask_id})
        return self.send_or_keep('POST', '/answer', body, 'answer:' + ask_id + ':' + str(body.get('status')), timeout)

    def _withdraw(self, body, timeout):
        """Take back a handoff this Machine sent: its id sealed for the Machine it went to, bound to that question, so
        the relay can't make one up and only this sender's seal opens as one. Kept in the outbox while the Network is
        away."""
        body = dict(body or {})
        ask_id, to, kind = str(body.get('id') or ''), str(body.get('to') or ''), str(body.get('kind') or 'handoff')
        group = self._group(body.get('fleet'))
        me = self._me(group)
        keys = self._current_keys(group, to) or self._keys_of(to)
        if not (self.private and me and ask_id and keys):
            raise ValueError("Their Machine has no keys on The Machine Network, so it can't be told; nothing was sent.")
        sealed = nc.seal({'id': ask_id}, self.private, keys, {'p': 'withdraw', 'k': kind, 'f': me, 't': to, 'r': ask_id})
        return self.send_or_keep('POST', '/withdraw', {'id': ask_id, 'sealed': sealed}, 'withdraw:' + ask_id, timeout)

    def send_or_keep(self, method, path, body, key, timeout=40):
        """Commit intent before delivery. Keep the exact intent if delivery is uncertain."""
        import fleet as fleet_module
        encoded = json.dumps(body, sort_keys=True)
        with self.fleet.connect() as db:
            # A final answer supersedes an unsent waiting acknowledgment, not the reverse.
            if path == '/answer' and body.get('status') != 'waiting':
                db.execute('DELETE FROM network_outbox WHERE key=?', ('answer:' + str(body.get('id')) + ':waiting',))
            elif path == '/answer' and db.execute("SELECT 1 FROM network_outbox WHERE path='/answer' AND json_extract(body,'$.id')=? AND json_extract(body,'$.status')!='waiting'", (body.get('id'),)).fetchone():
                return {'ok': True, 'queued': True}
            db.execute('INSERT INTO network_outbox(key,method,path,body,tries,next_at,created_at) VALUES(?,?,?,?,0,?,?) '
                       'ON CONFLICT(key) DO UPDATE SET method=excluded.method,path=excluded.path,body=excluded.body',
                       (key, method, path, encoded, _later(30), now()))
        try:
            result = self.inner.call(method, path, body, timeout=timeout)
        except (fleet_module.Unavailable, OSError) as error:
            with self.fleet.connect() as db:
                db.execute('UPDATE network_outbox SET error=? WHERE key=? AND body=?', (type(error).__name__, key, encoded))
            LOG.info('network kept for later path=%s error=%s', path, type(error).__name__)
            return {'ok': True, 'queued': True}
        except (ValueError, fleet_module.Off):
            with self.fleet.connect() as db:
                db.execute('DELETE FROM network_outbox WHERE key=? AND body=?', (key, encoded))
            raise
        with self.fleet.connect() as db:
            db.execute('DELETE FROM network_outbox WHERE key=? AND body=?', (key, encoded))
        return result

    def flush(self):
        """Retry committed intents with backoff; a stale retry never removes a newer one."""
        import fleet as fleet_module
        with self.fleet.connect() as db:
            rows = db.execute('SELECT * FROM network_outbox WHERE next_at<=? ORDER BY created_at LIMIT 20', (now(),)).fetchall()
        for row in rows:
            try:
                self.inner.call(row['method'], row['path'], json.loads(row['body']), timeout=30)
                done = True
            except (fleet_module.Unavailable, OSError) as error:
                done = False
                with self.fleet.connect() as db:
                    if row['created_at'] < (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=OUTBOX_DAYS)).isoformat(timespec='seconds'):
                        LOG.warning('network gave up path=%s after=%sd', row['path'], OUTBOX_DAYS)
                        db.execute('DELETE FROM network_outbox WHERE key=? AND body=?', (row['key'], row['body']))
                    else:
                        db.execute('UPDATE network_outbox SET tries=tries+1,next_at=?,error=? WHERE key=? AND body=?',
                                   (_later(min(3600, 30 * 2 ** min(row['tries'] + 1, 7))), type(error).__name__, row['key'], row['body']))
                break
            except (ValueError, fleet_module.Off):
                done = True  # closed/rejected by the relay; do not retry a terminal refusal
            if done:
                with self.fleet.connect() as db:
                    db.execute('DELETE FROM network_outbox WHERE key=? AND body=?', (row['key'], row['body']))

    def waiting(self):
        with self.fleet.connect() as db:
            return db.execute('SELECT COUNT(*) FROM network_outbox').fetchone()[0]

    # -- receiving
    def _open_inbox(self, found):
        self._refreshed = False
        for ask in (found or {}).get('asks', []):
            if ask.get('sealed') is not None:
                try:
                    ask['question'], me = self._open(ask.pop('sealed'), ask, 'ask', ask.get('request_id'))
                    with self.fleet.connect() as db:
                        db.execute('INSERT OR IGNORE INTO network_sealed(ask_id,kind,asker,me,at) VALUES(?,?,?,?,?)',
                                   (str(ask['id']), str(ask.get('kind')), str(ask.get('from')), me, now()))
                except nc.SealError as error:
                    LOG.warning('network question dropped member=%s reason=%s', ask.get('from'), error)
                    ask['question'] = {}
            else:
                if ask.get('question'):
                    LOG.warning('network unsealed question dropped member=%s', ask.get('from'))
                ask['question'] = {}
        for item in (found or {}).get('answers', []):
            if item.get('sealed') is None:
                if item.get('answer') is not None:
                    LOG.warning('network unsealed answer dropped member=%s', item.get('from'))
                    item['answer'], item['status'] = None, 'declined'
                continue
            try:
                item['answer'], _ = self._open(item.pop('sealed'), item, 'answer', item.get('id'))
            except nc.SealError as error:
                LOG.warning('network answer dropped member=%s reason=%s', item.get('from'), error)
                item['answer'], item['status'] = None, 'declined'
        for item in (found or {}).get('withdrawn', []) or []:
            # Taken back by whoever asked: only when it opens as theirs, names this question, and the question came
            # from them. Anything else is acknowledged and dropped.
            item['ok'] = False
            if item.get('sealed') is None:
                LOG.warning('network unsealed withdrawal dropped member=%s', item.get('from'))
                continue
            try:
                said, _ = self._open(item.pop('sealed'), item, 'withdraw', item.get('id'))
            except nc.SealError as error:
                LOG.warning('network withdrawal dropped member=%s reason=%s', item.get('from'), error)
                continue
            with self.fleet.connect() as db:
                asked = db.execute('SELECT asker,kind FROM network_sealed WHERE ask_id=?', (str(item.get('id')),)).fetchone()
            if not isinstance(said, dict) or said.get('id') != item.get('id') or (asked and (asked['asker'], asked['kind']) != (item.get('from'), item.get('kind'))):
                LOG.warning('network withdrawal does not match member=%s', item.get('from'))
                continue
            item['ok'] = True

    def _open(self, env, item, purpose, ref):
        """Opens what a teammate's Machine sealed, with the keys pinned for it; once per inbox, a fresh directory is
        fetched first when that fails (they joined or published their keys since): (body, my member id)."""
        for attempt in (0, 1):
            group = self._group(item.get('fleet_id') or item.get('fleet'))
            me, sender = self._me(group), self._keys_of(item.get('from'))
            try:
                if not sender or not me or not self.private:
                    raise nc.SealError('no keys for the sender')
                return nc.open_sealed(env, self.private, sender, {'p': purpose, 'k': item.get('kind'), 'f': item.get('from'), 't': me, 'r': ref}), me
            except nc.SealError:
                if attempt or self._refreshed:
                    raise
                self._refreshed = True
                self.fleet.directory(refresh=True)

    # -- once per question, ever
    def taken(self, ask_id):
        with self.fleet.connect() as db:
            return bool(db.execute('SELECT 1 FROM network_taken WHERE ask_id=?', (str(ask_id),)).fetchone())

    def take(self, ask_id, canonical=None):
        with self.fleet.connect() as db:
            db.execute('INSERT OR IGNORE INTO network_taken(ask_id,at) VALUES(?,?)', (str(ask_id), now()))
            if canonical:
                db.execute('INSERT OR IGNORE INTO network_twins(request_key,ask_id,at) VALUES(?,?,?)', (str(ask_id), str(canonical), now()))

    def tidy(self):
        cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=KEEP_DAYS)).isoformat(timespec='seconds')
        with self.fleet.connect() as db:
            db.execute('DELETE FROM network_taken WHERE at<?', (cut,))
            for table in ('network_twins', 'network_aliases', 'network_replies'):
                db.execute(f'DELETE FROM {table} WHERE at<?', (cut,))
            db.execute('DELETE FROM network_sealed WHERE at<?', (cut,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='network_request_nodes'").fetchone():
                db.execute('DELETE FROM network_request_nodes WHERE at<?', (cut,))

    def view(self):
        changes = self.fleet.meta('network_key_changes', [])
        return {'sealed': bool(self.private), 'fingerprint': nc.check_keys(self.public)['fingerprint'] if self.public else None,
                'key_changes': changes[:5], 'outbox': self.waiting()}


def wrap(inner, fleet, config):
    """The Fleet's relay connection, always sealing. A Machine that can't keep keys (no database path, or they can't be
    read or made) sends nothing on the Network and opens nothing from it."""
    path, private = keys_path(config), None
    try:
        private = nc.load_or_make(path) if path else None
    except OSError as error:
        LOG.warning('network keys unavailable error=%s', type(error).__name__)
    return Sealing(inner, fleet, private)


