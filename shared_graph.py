"""The Shared Graph: a Work Fleet's Machines share their Work records, timelines and client email with each other,
sealed Machine to Machine (docs/shared-graph.md). Mixed into fleet.Fleet, next to shared_connectors.SharedConnectors.

What goes, per thread, judged by the review by meaning (share_threads), with the owner's hand choice winning:
- client (someone outside the company, at an organisation on the Graph or with a work domain): the whole email;
- sensitive client: only a neutral line that it exists ("Private thread with Lee, 12 Oct");
- internal (colleagues only): nothing, also not by hand;
- personal: nothing, not even that it exists. Personal accounts are never read for this.
Records (people, organisations, projects with their board) go when they are Work, unless Keep private (share_private).

How it goes: every blob is encrypted with a fresh key, signed by this Machine, and that key is sealed
(network_crypto.seal) for each member a confirmed admin vouched for (fleet_trust.members). The records live on the
Machines: each one keeps what it published and what teammates shared with it (team_blobs, as signed). The Network is
only the mailbox: it carries the sealed blobs, can't read, change or replay them, and deletes each once every Machine
it was for took it (this Machine acks what it took), or after KEEP_DAYS. A Machine away longer than that, or one just
confirmed, asks its teammates (a sealed 'want'), and any of them passes on what it holds, newest first, in batches,
each blob exactly as its publisher signed it. Addresses inside records are keyed hashes with the Fleet graph key, which
only the members' Machines have; a confirmed admin's Machine makes it, seals it the same way, and makes a new one when
someone leaves."""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import secrets
import time

import fleet_trust as ft
import graph
import network_crypto as nc
import security_levels

LOG = logging.getLogger('personal-assistant')
TAG = b'tm-shared-graph/1\n'
KINDS = ('key', 'snapshot', 'delta', 'mail', 'file', 'ask', 'want')
TAGGED = ('mail', 'file', 'ask', 'want')  # one blob per tag: a newer one replaces it, an empty one says it's gone
RELAYED = ('snapshot', 'delta', 'mail', 'file')  # what a teammate's Machine passes on to someone who missed it
KEEP_DAYS = 30              # the Network keeps a blob nobody took this long, then drops it
WANT_DAYS = 7               # a Machine asks its teammates for what it missed this long
SERVE_BATCH = 40            # blobs this Machine passes on per round, for one teammate who asked
VERDICTS = ('client', 'sensitive', 'internal', 'personal')
HANDS = ('team', 'private')
ROUND_EVERY = 300           # publish and fetch, at most this often
SNAPSHOT_EVERY = 86400      # a full snapshot at least daily
MAX_RECORDS = 3000
MAX_THREADS = 20000         # all the Work history the Machine has, newest first
PER_HOUR, PER_DAY = 100, 1800  # blobs this Machine puts, under the Network's own limits (120 and 2000): a backfill takes days
MAX_FILE = 2560 * 1024     # bytes of one attachment that goes with a shared thread; bigger ones say "too large to share"
MAX_MESSAGES = 30           # per thread
MAX_BODY = 20000            # characters of one message
TIMELINE = 12               # lines per record
MAIL_KINDS = ('gmail.message', 'ms_graph.message', 'outlook.message', 'salesflare.email', 'intercom.conversation')
HEX = re.compile(r'[a-f0-9]{32}')
ADDRESS = re.compile(r'[^@\s<>",;]+@[^@\s<>",;]+\.[^@\s<>",;]+')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS share_threads(thread TEXT PRIMARY KEY,verdict TEXT,hand TEXT,hand_at TEXT,event_id INTEGER,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS share_lines(event_id INTEGER PRIMARY KEY,thread TEXT,line TEXT NOT NULL,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS share_private(node_id TEXT PRIMARY KEY,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS share_out(fleet TEXT NOT NULL,kind TEXT NOT NULL,key TEXT NOT NULL,hash TEXT NOT NULL,at TEXT NOT NULL,
  PRIMARY KEY(fleet,kind,key));
CREATE TABLE IF NOT EXISTS team_records(fleet TEXT NOT NULL,member TEXT NOT NULL,rid TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,
  at TEXT NOT NULL,PRIMARY KEY(fleet,member,rid));
CREATE TABLE IF NOT EXISTS team_mail(fleet TEXT NOT NULL,member TEXT NOT NULL,tag TEXT NOT NULL,seq INTEGER NOT NULL,body TEXT NOT NULL,
  at TEXT NOT NULL,PRIMARY KEY(fleet,member,tag));
CREATE TABLE IF NOT EXISTS team_sources(fleet TEXT NOT NULL,member TEXT NOT NULL,name TEXT,sign TEXT,snapshot INTEGER NOT NULL DEFAULT 0,
  seq INTEGER NOT NULL DEFAULT 0,threads TEXT NOT NULL DEFAULT '[]',left_at TEXT,at TEXT NOT NULL,PRIMARY KEY(fleet,member));
CREATE TABLE IF NOT EXISTS team_files(fleet TEXT NOT NULL,member TEXT NOT NULL,tag TEXT NOT NULL,seq INTEGER NOT NULL,name TEXT,mime TEXT,
  size INTEGER NOT NULL DEFAULT 0,data TEXT NOT NULL,at TEXT NOT NULL,PRIMARY KEY(fleet,member,tag));
CREATE TABLE IF NOT EXISTS team_asks(fleet TEXT NOT NULL,member TEXT NOT NULL,off INTEGER NOT NULL,asked TEXT NOT NULL,seq INTEGER NOT NULL,
  at TEXT NOT NULL,PRIMARY KEY(fleet,member));
CREATE TABLE IF NOT EXISTS team_pending(id TEXT PRIMARY KEY,item TEXT NOT NULL,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS team_blobs(id TEXT PRIMARY KEY,fleet TEXT NOT NULL,member TEXT NOT NULL,kind TEXT NOT NULL,tag TEXT,
  seq INTEGER NOT NULL,dk TEXT NOT NULL,header TEXT NOT NULL,sha TEXT NOT NULL,ct TEXT NOT NULL,sig TEXT NOT NULL,at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS team_blobs_by ON team_blobs(fleet,member,kind,tag);
CREATE TABLE IF NOT EXISTS team_wants(fleet TEXT NOT NULL,member TEXT NOT NULL,body TEXT NOT NULL,seq INTEGER NOT NULL,at TEXT NOT NULL,
  PRIMARY KEY(fleet,member));
'''


PENDING_DAYS = 7           # a blob from a teammate this Machine doesn't know as confirmed yet waits this long


class Later(ValueError):
    """Not now: from a teammate whose confirmation this Machine hasn't seen yet. Kept and tried again."""


def ensure(db):
    db.executescript(SCHEMA)
    import message_seal
    if message_seal.is_protected(db):  # teammates' mail, records, files and the review's lines: sealed at rest like the owner's own
        message_seal.ensure_triggers(db)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def keyed(key, value):
    """A keyed hash: an address or domain as teammates' Machines can match it and the Network can't enumerate it."""
    return hmac.new(nc.unb64(key, 32) if isinstance(key, str) else key, str(value).strip().lower().encode(), hashlib.sha256).hexdigest()[:32]


# ---------------------------------------------------------------- blobs
def _keys(dk, nonce, header):
    return nc._hkdf(dk, nonce, b'tm-shared-graph/1' + canon(header))


def _stream(key, nonce, data):
    """network_crypto._stream, byte for byte, quick enough for an attachment: the pad in one go, XORed as integers."""
    if not data:
        return b''
    base = hmac.new(key, nonce, hashlib.sha256)
    def block(i):
        mac = base.copy()
        mac.update(i.to_bytes(8, 'big'))
        return mac.digest()
    pad = b''.join(map(block, range(0, len(data), 32)))[:len(data)]
    return (int.from_bytes(data, 'big') ^ int.from_bytes(pad, 'big')).to_bytes(len(data), 'big')


def encrypt(dk, header, body):
    nonce = secrets.token_bytes(16)
    keys = _keys(dk, nonce, header)
    ct = _stream(keys[:32], nonce, canon(body))
    return nonce + hmac.new(keys[32:], canon(header) + nonce + ct, hashlib.sha256).digest() + ct


def decrypt(dk, header, data):
    if len(data) < 48:
        raise nc.SealError('not a blob')
    nonce, mac, ct = data[:16], data[16:48], data[48:]
    keys = _keys(dk, nonce, header)
    if not hmac.compare_digest(hmac.new(keys[32:], canon(header) + nonce + ct, hashlib.sha256).digest(), mac):
        raise nc.SealError('changed on the way')
    try:
        return json.loads(_stream(keys[:32], nonce, ct))
    except ValueError:
        raise nc.SealError('not readable') from None


def digest(header, data):
    return hashlib.sha256(canon(header) + data).hexdigest()


def _signed(header, data):
    return TAG + canon(header) + hashlib.sha256(data).digest()


def wrap_context(header, wrapper, member):
    return {'p': 'graph', 'k': header['kind'], 'f': wrapper, 't': member, 'r': header['id']}


def make(private, header, body, recipients):
    """A blob: header in the clear, the body encrypted with a fresh key, signed by this Machine, and that key sealed for
    each recipient ({member id: their public keys})."""
    dk = secrets.token_bytes(32)
    data = b'' if body is None else encrypt(dk, header, body)  # None: a tombstone, no longer shared (an empty blob)
    h = digest(header, data)
    wraps = {mid: nc.seal({'dk': nc.b64(dk), 'h': h}, private, keys, wrap_context(header, header['from'], mid)) for mid, keys in recipients.items()}
    return {'header': header, 'ct': nc.b64(data), 'sig': nc.b64(nc.ed25519_sign(nc.unb64(private['sign'], 32), _signed(header, data))),
            'wraps': wraps}, nc.b64(dk)


def open_blob(item, private, me, wrapper_keys, sender_sign):
    """(header, body, dk) of a blob for this Machine, or SealError. wrapper_keys: the public keys of whoever sealed the
    wrap (the publisher, or a teammate passing it on); sender_sign: the publisher's sign key (base64), or None to take
    the one an admin vouched for inside the wrap (someone who left)."""
    header, wrap = item.get('header'), item.get('wrap')
    if not isinstance(header, dict) or header.get('v') != 1 or header.get('kind') not in KINDS or not HEX.fullmatch(str(header.get('id'))):
        raise nc.SealError('not a blob header')
    data = nc.unb64(item.get('ct'))
    inner = nc.open_sealed(wrap, private, wrapper_keys, wrap_context(header, item.get('by') or header['from'], me))
    if not isinstance(inner, dict) or inner.get('h') != digest(header, data):
        raise nc.SealError('the key is for another blob')
    sign = sender_sign or inner.get('from_key')
    if not isinstance(sign, str) or not nc.ed25519_verify(nc.unb64(sign, 32), _signed(header, data), nc.unb64(item.get('sig'), 64)):
        raise nc.SealError('not signed by its publisher')
    dk = nc.unb64(inner.get('dk'), 32)
    if not data:
        if header['kind'] not in TAGGED:
            raise nc.SealError('empty')
        return header, {}, inner['dk']
    return header, decrypt(dk, header, data), inner['dk']


def keep_blob(db, header, ct, sig, dk):
    """This Machine's own copy of a blob as its publisher signed it, to pass on to a teammate who missed it: the newest
    of each thread or file, the last snapshot and the deltas since. Keys, asks and wants are never passed on."""
    kind, fleet, member, seq = header['kind'], header['fleet'], header['from'], int(header['seq'])
    if kind not in RELAYED:
        return
    if kind == 'snapshot':
        db.execute("DELETE FROM team_blobs WHERE fleet=? AND member=? AND kind IN ('snapshot','delta') AND seq<?", (fleet, member, seq))
    elif kind in TAGGED:
        db.execute('DELETE FROM team_blobs WHERE fleet=? AND member=? AND kind=? AND tag=? AND seq<?', (fleet, member, kind, header.get('tag'), seq))
        # Keep the signed tombstone too. A teammate who missed it must not regain the previous content
        # through catch-up, and its sequence remains the high-water mark after the visible row is gone.
    db.execute('INSERT OR IGNORE INTO team_blobs(id,fleet,member,kind,tag,seq,dk,header,sha,ct,sig,at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (header['id'], fleet, member, kind, header.get('tag'), seq, dk, json.dumps(header), digest(header, nc.unb64(ct)), ct, sig, now()))


def _iso_ago(days):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- threads: what the review said and the owner chose
def thread_of(kind, metadata, event_id):
    key, _ = graph.thread_key(kind, metadata or {})
    return key or f'ev:{event_id}'


def judge(db, team, at=None):
    """The review's per-thread call (finish_analysis team): [{event_id, share, line}]. share: client, sensitive, internal
    or personal, judged by meaning; line: one short line of what happened, for the timeline, never the text itself.
    The owner's hand choice stays."""
    stamp = at or now()
    kept = 0
    for item in team or ():
        if not isinstance(item, dict) or not str(item.get('event_id') or '').isdigit():
            raise ValueError('team is [{"event_id", "share", "line"}]')
        row = db.execute('SELECT id,kind,metadata FROM events WHERE id=?', (int(item['event_id']),)).fetchone()
        if not row:
            continue
        verdict = item.get('share')
        if verdict not in VERDICTS:
            raise ValueError('share: ' + ', '.join(VERDICTS))
        thread = thread_of(row['kind'], json.loads(row['metadata'] or '{}'), row['id'])
        # Personal stays personal: a later review never turns it into a client or sensitive call (not even a "Private
        # thread" line); only the owner's hand can. Sensitive never becomes client.
        db.execute('INSERT INTO share_threads(thread,verdict,event_id,at) VALUES(?,?,?,?) ON CONFLICT(thread) DO UPDATE SET '
                   'verdict=CASE WHEN share_threads.verdict=\'personal\' THEN \'personal\' '
                   'WHEN share_threads.verdict=\'sensitive\' AND excluded.verdict=\'client\' THEN share_threads.verdict '
                   'ELSE excluded.verdict END,event_id=excluded.event_id,at=excluded.at', (thread, verdict, row['id'], stamp))
        verdict = db.execute('SELECT verdict FROM share_threads WHERE thread=?', (thread,)).fetchone()[0]
        line = graph.one_line(item.get('line'), 160)
        if line and verdict == 'client':
            db.execute('INSERT OR REPLACE INTO share_lines(event_id,thread,line,at) VALUES(?,?,?,?)', (row['id'], thread, line, stamp))
        elif verdict != 'client':
            db.execute('DELETE FROM share_lines WHERE thread=?', (thread,))
        kept += 1
    return kept


def set_hand(db, event_id, choice):
    """The owner's choice for a thread: team, private, or None for the review's call again."""
    if choice not in (*HANDS, None):
        raise ValueError('Choose team or private')
    row = db.execute('SELECT id,kind,metadata FROM events WHERE id=?', (int(event_id),)).fetchone()
    if not row:
        raise ValueError('Unknown message')
    thread = thread_of(row['kind'], json.loads(row['metadata'] or '{}'), row['id'])
    db.execute('INSERT INTO share_threads(thread,hand,hand_at,event_id,at) VALUES(?,?,?,?,?) ON CONFLICT(thread) DO UPDATE SET '
               'hand=excluded.hand,hand_at=excluded.hand_at', (thread, choice, now() if choice else None, row['id'], now()))
    return thread


def keep_private(db, node_id, on=True):
    if not db.execute('SELECT 1 FROM graph_nodes WHERE id=?', (str(node_id),)).fetchone():
        raise ValueError('Not in the graph')
    if on:
        db.execute('INSERT OR REPLACE INTO share_private(node_id,at) VALUES(?,?)', (str(node_id), now()))
    else:
        db.execute('DELETE FROM share_private WHERE node_id=?', (str(node_id),))
    return bool(on)


def addresses(metadata):
    found = []
    for field in ('from', 'to', 'cc'):
        value = metadata.get(field)
        for part in value if isinstance(value, list) else [value]:
            found += [a.lower() for a in ADDRESS.findall(str(part or ''))]
    return found


def outside(address, internal):
    domain = address.rsplit('@', 1)[-1]
    return not any(domain == d or domain.endswith('.' + d) for d in internal)


class Scope:
    """What this Machine may share with one Work Fleet: its accounts, its own domains, who is private."""

    def __init__(self, db, accounts, internal, mine, personal=()):
        """personal: graph people of the owner's own life (only in their Family or Friends): never a record, and
        threads with them stay out, like Keep private. So does anyone the owner marked Personal; one they marked
        Work is not."""
        self.accounts, self.internal, self.mine = set(accounts), set(internal), {m.lower() for m in mine}
        self.private = {r[0] for r in db.execute('SELECT node_id FROM share_private')}
        personal = set(personal)
        # Only-you projects (their own visibility or their board's): never shared, and neither are their threads
        self.private |= {'project:' + str(i) for i in graph.hidden_project_ids(db)}
        self.hidden_events = graph.hidden_event_ids(db)
        self.calls = {r[0]: (r[1], r[2]) for r in db.execute('SELECT thread,verdict,hand FROM share_threads')}  # the review's and the owner's
        self.private_ids = set()
        self.org_of = {}
        for row in db.execute("SELECT id,kind,info FROM graph_nodes WHERE kind IN ('person','org')"):
            info = json.loads(row['info'] or '{}')
            ids = {row['id'], *info.get('identifiers', [])}
            if row['kind'] == 'person':
                if info.get('side_by') == 'owner' and info.get('side') == 'personal' or row['id'] in personal and info.get('side_by') != 'owner':
                    self.private.add(row['id'])  # the owner's word (Work or Personal) wins over Family or Friends
                for i in ids:
                    if str(i).startswith('email:') and info.get('org'):
                        self.org_of[i[6:].lower()] = info['org']
                if row['id'] in self.private or (info.get('org') and info['org'] in self.private):
                    self.private_ids |= {i[6:].lower() for i in ids if str(i).startswith('email:')}
            elif row['id'] in self.private and info.get('domain'):
                self.private_ids.add('@' + info['domain'])

    def private_address(self, address):
        return address in self.private_ids or '@' + address.rsplit('@', 1)[-1] in self.private_ids

    def client_address(self, address):
        """Someone outside the company, with a work domain or at an organisation on the Graph."""
        if address in self.mine or not outside(address, self.internal):
            return False
        return address.rsplit('@', 1)[-1] not in graph.FREEMAIL or bool(self.org_of.get(address))


def visibility(db, scope, event):
    """A thread's visibility for one Fleet: None (not shared at all, no label: another account or personal), 'internal'
    (colleagues only, never re-shared), 'team' (the whole thread), 'line' (sensitive: that it exists), 'private'
    (Only you: by hand, or nobody outside who is a client), 'pending' (Only you until the review judged it), 'personal'
    (the review found it is the owner's own life: nothing of it goes, not even that it exists)."""
    if event['account_id'] not in scope.accounts:
        return None
    if event['id'] in getattr(scope, 'hidden_events', ()):
        return 'private'  # a message of an Only-you project or board
    metadata = event['metadata'] if isinstance(event['metadata'], dict) else json.loads(event['metadata'] or '{}')
    people = addresses(metadata)
    if any(scope.private_address(a) for a in people):
        return 'private'
    verdict, hand = scope.calls.get(thread_of(event['kind'], metadata, event['id']), (None, None))
    if verdict == 'personal' and not hand:
        return 'personal'
    if people and not any(outside(a, scope.internal) and a not in scope.mine for a in people):
        return 'internal'
    if hand == 'private':
        return 'private'
    if hand == 'team':
        return 'team'
    if not any(scope.client_address(a) for a in people) and event['kind'] != 'intercom.conversation':
        return 'private'
    return {'client': 'team', 'sensitive': 'line', 'internal': 'internal'}.get(verdict, 'pending')


def label(view):
    """The dashboard's label for a thread: Team, Only you, or none."""
    return None if view in (None, 'internal') else 'team' if view == 'team' else 'only_you'


# ---------------------------------------------------------------- what this Machine shares
def _first_name(name):
    return (str(name or '').split() or [''])[0][:40]


def _who(metadata, scope):
    for a in addresses(metadata):
        if outside(a, scope.internal) and a not in scope.mine:
            raw = str(metadata.get('from') or '')
            if a in raw.lower() and '<' in raw:
                return _first_name(raw.split('<', 1)[0].strip().strip('"')) or a.split('@')[0]
            return a.split('@')[0].split('.')[0].capitalize()
    return ''


def private_line(who, at):
    when = graph.parse_time(at)
    day = f'{when.day} {when:%b}' if when else ''
    return ('Private thread with ' + who if who else 'Private thread') + (', ' + day if day else '')


def _events(db, accounts, since):
    if not accounts:
        return []
    return db.execute(f"SELECT id,account_id,kind,occurred_at,title,body,metadata FROM events WHERE deleted=0 AND occurred_at>=? "
                      f"AND account_id IN ({','.join('?' * len(accounts))}) ORDER BY occurred_at", (since, *accounts)).fetchall()


def collect(db, scope, key, secret, today=None):
    """Everything this Machine shares with one Fleet: (records {rid: record}, threads {tag: body}, stats, files). key: the
    Fleet graph key (hashes); secret: this Machine's own (record ids and thread tags). Threads go as far back as the
    Machine has mail, the newest first. files: {tag: [(event id, account id, kind)]}, the messages of each whole thread,
    whose attachments go with it (fetched when it is sent, never for a sensitive one)."""
    today = today or dt.datetime.now(dt.timezone.utc)
    rid = lambda local: keyed(secret, 'record|' + local)
    keys = key if isinstance(key, list) else [key]
    key = keys[0]
    every = lambda value: [keyed(k, value) for k in keys]  # with every graph key held: teammates on another one still match
    views, threads, by_thread = {}, collections.defaultdict(list), {}
    for e in _events(db, sorted(scope.accounts), ''):
        e = dict(e)
        e['metadata'] = json.loads(e['metadata'] or '{}')
        view = visibility(db, scope, e)
        views[e['id']] = view
        if view in ('team', 'line'):
            thread = thread_of(e['kind'], e['metadata'], e['id'])
            threads[thread].append(e)
            by_thread[e['id']] = thread
    lines = {r['event_id']: r['line'] for r in db.execute('SELECT event_id,line FROM share_lines')}
    out_threads, files = {}, {}
    newest = sorted(threads.items(), key=lambda t: t[1][-1]['occurred_at'], reverse=True)[:MAX_THREADS]
    for thread, items in newest:
        tag = keyed(secret, 'thread|' + thread)
        last = items[-1]
        if views[last['id']] == 'line':
            out_threads[tag] = {'tag': tag, 'private': True, 'at': last['occurred_at'], 'line': private_line(_who(last['metadata'], scope), last['occurred_at'])}
            continue
        if last['kind'] not in MAIL_KINDS:
            continue
        messages = [{'at': e['occurred_at'], 'from': str(e['metadata'].get('from') or '')[:200], 'to': str(e['metadata'].get('to') or '')[:500],
                     'cc': str(e['metadata'].get('cc') or '')[:500], 'body': str(e['body'] or '')[:MAX_BODY]}
                    for e in items[-MAX_MESSAGES:]]
        out_threads[tag] = {'tag': tag, 'subject': str(items[0]['title'] or '')[:200], 'at': last['occurred_at'], 'messages': messages}
        files[tag] = [(e['id'], e['account_id'], e['kind']) for e in items[-MAX_MESSAGES:]]

    def thread_tags(node_id):
        tags = []
        for (event_id,) in db.execute('SELECT event_id FROM graph_events WHERE node_id=? AND probable=0 ORDER BY at DESC', (node_id,)):
            if views.get(event_id) == 'team' and event_id in by_thread:
                tag = keyed(secret, 'thread|' + by_thread[event_id])
                if tag in out_threads and tag not in tags:
                    tags.append(tag)
        return tags[:40]

    def timeline(node_id):
        found = []
        for (event_id, at) in db.execute('SELECT event_id,at FROM graph_events WHERE node_id=? AND probable=0 ORDER BY at DESC LIMIT 200', (node_id,)):
            view = views.get(event_id)
            if view == 'team' and lines.get(event_id):
                found.append({'at': at, 'line': lines[event_id]})
            elif view == 'line' and event_id in by_thread:
                tag = keyed(secret, 'thread|' + by_thread[event_id])
                if not any(x.get('tag') == tag for x in found):
                    found.append({'at': at, 'line': out_threads.get(tag, {}).get('line') or 'Private thread', 'tag': tag, 'private': True})
            if len(found) >= TIMELINE:
                break
        return found

    def work(node_id):
        """(shared, last): a record goes when one of its threads is shared, or it is a client the review hasn't judged
        every thread of yet; not when its threads are only sensitive, internal or kept to the owner by hand."""
        seen = [(views.get(r[0]), r[1]) for r in db.execute('SELECT event_id,at FROM graph_events WHERE node_id=? AND probable=0', (node_id,))]
        seen = [(v, at) for v, at in seen if v in ('team', 'pending')]
        return bool(seen), max((at for _, at in seen), default=None)

    records = {}
    people = {}
    for row in db.execute("SELECT * FROM graph_nodes WHERE kind='person' ORDER BY strength DESC LIMIT ?", (MAX_RECORDS,)):
        info = json.loads(row['info'] or '{}')
        emails = sorted({i[6:].lower() for i in [row['id'], *info.get('identifiers', [])] if str(i).startswith('email:')})
        if (row['id'] in scope.private or not emails or info.get('colleague') or any(not outside(e, scope.internal) or e in scope.mine for e in emails)
                or str(info.get('role') or '').lower() == 'automated sender' or any(graph.bulk(e) for e in emails)
                or (info.get('org') and info['org'] in scope.private)):
            continue
        ok, last = work(row['id'])
        if not ok:
            continue
        org = db.execute('SELECT name,info FROM graph_nodes WHERE id=?', (info['org'],)).fetchone() if info.get('org') else None
        org_domain = json.loads(org['info'] or '{}').get('domain') if org else None
        kept = db.execute('SELECT 1 FROM graph_names WHERE node_id IN (SELECT value FROM json_each(?))', (json.dumps([row['id'], *info.get('identifiers', [])]),)).fetchone()
        domains = sorted({e.rsplit('@', 1)[1] for e in emails} - graph.FREEMAIL)
        r = rid(row['id'])
        people[row['id']] = [h for e in emails for h in every('email:' + e)]
        records[r] = {'r': r, 't': 'person', 'name': row['name'], 'name_hand': bool(kept), 'role': info.get('role'), 'role_hand': info.get('role_by') == 'owner',
                      'org': {'name': org['name'], **({'d': keyed(key, 'domain:' + org_domain)} if org_domain else {})} if org else None,
                      'org_hand': info.get('org_by') == 'owner', 'ids': people[row['id']], 'd': [h for d in domains for h in every('domain:' + d)],
                      'score': round(row['strength'], 3), 'last': (last or row['last_at'] or '')[:10], 'timeline': timeline(row['id']),
                      'threads': thread_tags(row['id'])}
    shared_orgs = {json.loads(db.execute('SELECT info FROM graph_nodes WHERE id=?', (p,)).fetchone()['info']).get('org') for p in people}
    for row in db.execute("SELECT * FROM graph_nodes WHERE kind='org'"):
        info = json.loads(row['info'] or '{}')
        domain = info.get('domain')
        if row['id'] not in shared_orgs or row['id'] in scope.private or not domain or domain in graph.FREEMAIL or not outside('x@' + domain, scope.internal):
            continue
        r = rid(row['id'])
        records[r] = {'r': r, 't': 'org', 'name': row['name'], 'name_hand': 'name' in (info.get('edited') or []), 'role': info.get('role'),
                      'role_hand': info.get('role_by') == 'owner', 'd': every('domain:' + domain), 'score': round(row['strength'], 3),
                      'last': (row['last_at'] or '')[:10]}
    tiles = {t['id']: t for b in graph.boards(db, today.isoformat()) for t in b['projects']}
    team_boards = graph.team_board_ids(db)
    for row in db.execute("SELECT n.*,p.id pid,b.id bid,p.stage,p.stage_by,p.stage_at,p.stage_reason,p.next_step,p.org porg,b.name board,b.stages stages,b.side side "
                          "FROM graph_nodes n JOIN projects p ON 'project:'||p.id=n.id LEFT JOIN boards b ON b.id=p.board_id "
                          "WHERE n.kind='project' AND p.closed_at IS NULL"):
        if row['id'] in scope.private:
            continue
        ok, last = work(row['id'])
        # A project on a board the team sees goes with its board record (stage, why, next, organisation) even before a
        # thread of it is shared; its threads and timeline still only when shared. Never one of the owner's own life.
        on_team_board = bool(row['board']) and row['bid'] in team_boards and not any(
            views.get(r[0]) == 'personal' for r in db.execute('SELECT event_id FROM graph_events WHERE node_id=? AND probable=0', (row['id'],)))
        if not ok and not on_team_board:
            continue
        linked = [l['a'] if l['b'] == row['id'] else l['b'] for l in db.execute('SELECT a,b FROM graph_links WHERE (a=? OR b=?) AND kind=?',
                                                                                  (row['id'], row['id'], 'project'))]
        if any(p in scope.private for p in linked):
            continue
        r = rid(row['id'])
        tile = tiles.get(row['id']) or {}
        domain = row['porg'] or (str((tile.get('org') or {}).get('node_id') or '')[4:] or None)
        org = graph.org_card(db, domain) if domain else None
        records[r] = {'r': r, 't': 'project', 'name': row['name'], 'score': round(row['strength'], 3), 'last': (last or '')[:10],
                      'board': row['board'], 'stages': graph.stage_names(row) if row['board'] else None,
                      'stage': row['stage'] if row['board'] else None, 'stage_hand': row['stage_by'] == 'owner', 'stage_at': row['stage_at'],
                      'why': row['stage_reason'], 'next': row['next_step'],
                      'org': {'name': org['name'], 'd': keyed(key, 'domain:' + domain)} if org else None,
                      'people': sorted({h for p in linked for h in people.get(p, [])}), 'timeline': timeline(row['id']), 'threads': thread_tags(row['id'])}
    week = (today - dt.timedelta(days=7)).isoformat()
    recent = {}
    for thread, items in threads.items():
        if items[-1]['occurred_at'] >= week:
            recent[thread] = views[items[-1]['id']]
    held_private = {r['thread'] for r in db.execute("SELECT thread FROM share_threads WHERE hand='private' AND hand_at>=?", (week,))}
    stats = {'threads': sum(1 for v in recent.values() if v == 'team'), 'held': sum(1 for v in recent.values() if v == 'line') + len(held_private),
             'people': sum(1 for r in records.values() if r['t'] == 'person'), 'orgs': sum(1 for r in records.values() if r['t'] == 'org'),
             'projects': sum(1 for r in records.values() if r['t'] == 'project')}
    return records, out_threads, stats, files


def unjudged(db, scope, limit, skip=()):
    """Work client threads the review hasn't judged yet, the newest first: for the history pass (one run judges a batch,
    the next one goes on), so all the Work history the Machine has goes through the same review labels."""
    found, seen = [], set(skip)
    if not scope.accounts:
        return found
    rows = db.execute(f"SELECT id,account_id,kind,occurred_at,title,body,metadata FROM events WHERE deleted=0 "
                      f"AND account_id IN ({','.join('?' * len(scope.accounts))}) ORDER BY occurred_at DESC", sorted(scope.accounts))
    for e in rows:
        if e['kind'] not in MAIL_KINDS:
            continue
        e = dict(e)
        e['metadata'] = json.loads(e['metadata'] or '{}')
        thread = thread_of(e['kind'], e['metadata'], e['id'])
        if thread in seen:
            continue
        seen.add(thread)
        if visibility(db, scope, e) != 'pending':
            continue
        found.append({'event_id': e['id'], 'subject': str(e['title'] or '')[:200], 'with': _who(e['metadata'], scope), 'at': e['occurred_at'],
                      'from': str(e['metadata'].get('from') or '')[:200], 'to': str(e['metadata'].get('to') or '')[:300],
                      'text': ' '.join(str(e['body'] or '').split())[:700]})
        if len(found) >= limit:
            break
    return found


# ---------------------------------------------------------------- the team's view: Mine plus what teammates shared
def pick(candidates):
    """The value of one field among sources: one set by hand wins (the newest hand), else the closest source's."""
    candidates = [c for c in candidates if c.get('value') not in (None, '')]
    if not candidates:
        return None
    hand = [c for c in candidates if c.get('hand')]
    best = max(hand or candidates, key=lambda c: (c.get('score') or 0, c.get('at') or ''))
    return best


def pick_stage(candidates):
    """A tile's stage: the newest move by hand, else the newest move a message showed (stages are facts with dates)."""
    candidates = [c for c in candidates if c.get('value')]
    if not candidates:
        return None
    hand = [c for c in candidates if c.get('hand')]
    return max(hand or candidates, key=lambda c: (c.get('at') or '', c.get('score') or 0))


class SharedGraph:
    """Mixed into fleet.Fleet: the rounds, the owner's controls and the team's view."""

    # -- keys and members
    def _graph_db(self):
        with self.connect() as db:
            ensure(db)

    def graph_secret(self):
        found = self.meta('graph_secret')
        if not found:
            found = nc.b64(secrets.token_bytes(32))
            self.set_meta('graph_secret', found)
        return found

    def graph_keys(self, fleet):
        return self.meta('graph_keys:' + fleet, {}) or {}

    def graph_key(self, fleet):
        """(kv, key) of the newest Fleet graph key this Machine has, else (None, None)."""
        keys = self.graph_keys(fleet)
        if not keys:
            return None, None
        kv = max(keys, key=lambda k: (keys[k]['at'], k))
        return kv, keys[kv]['key']

    def confirmed(self, group):
        """(confirmed members {id: sign key}, trusted admins {id: key}, waiting [ids]) for a Work Fleet entry."""
        trusted, _ = self.trust(group)
        found, waiting, pins = ft.members(group, trusted, self.meta('graph_members:' + group['id']))
        self.set_meta('graph_members:' + group['id'], pins)
        return found, trusted, waiting

    def _public(self, group, mid):
        m = next((x for x in group.get('members', []) if x.get('member_id') == mid), None)
        return m.get('keys') if m and m.get('keys') and nc.check_keys(m['keys']) else None

    def _seq(self, fleet):
        seq = max(int(time.time() * 1000), int(self.meta('graph_seq:' + fleet, 0) or 0) + 1)
        self.set_meta('graph_seq:' + fleet, seq)
        return seq

    def _header(self, group, me, kind, to, **extra):
        return {'v': 1, 'fleet': group['id'], 'from': me['member_id'], 'id': secrets.token_hex(16), 'seq': self._seq(group['id']),
                'kind': kind, 'at': now(), 'to': sorted(to), **extra}

    def _put(self, group, me, kind, body, to, **extra):
        recipients = {mid: keys for mid in to if (keys := self._public(group, mid))}
        if not recipients:
            return None
        header = self._header(group, me, kind, recipients, **extra)
        blob, dk = make(self._private(), header, body, recipients)
        self.relay.call('POST', '/graph', {'op': 'put', 'fleet': group['id'], 'blob': blob}, timeout=120 if kind == 'file' else 60)
        self._spent()
        with self.connect() as db:
            ensure(db)
            keep_blob(db, header, blob['ct'], blob['sig'], dk)  # this Machine passes on its own, too
        return header

    def _spent(self):
        stamp = time.time()
        self.set_meta('graph_puts', [t for t in (self.meta('graph_puts', []) or []) if t > stamp - 86400][-PER_DAY:] + [stamp])

    def _budget(self):
        """How many more blobs may go now: PER_HOUR and PER_DAY over this Machine's own puts. A long history goes over days."""
        stamp = time.time()
        puts = [t for t in (self.meta('graph_puts', []) or []) if t > stamp - 86400]
        return max(0, min(PER_HOUR - sum(1 for t in puts if t > stamp - 3600), PER_DAY - len(puts)))

    # -- every round
    def graph_round(self, force=False):
        """For each Work Fleet: ask the owner about members to confirm, keep the graph key current, publish what changed,
        and take what teammates published. Never anything for someone outside the Fleet."""
        if not self._private():
            return
        stamp = time.monotonic()
        if not force and stamp - self.last.get('graph', -1e9) < ROUND_EVERY:
            return
        self.last['graph'] = stamp
        self._graph_db()
        groups = [g for g in (self.directory().get('fleets') or []) if g.get('kind') == 'work' and g.get('id')]
        self._graph_forget(groups)
        for group in groups:
            me = self._me_in(group)
            if not me:
                continue
            try:
                self._graph_fleet(group, me)
            except Exception as error:  # noqa: BLE001 - away, or a Network from before: the next round tries again
                LOG.warning('shared graph round fleet=%s error=%s', group['id'], type(error).__name__)
        try:
            self.graph_fetch()
        except Exception as error:  # noqa: BLE001
            LOG.warning('shared graph fetch error=%s', type(error).__name__)

    def _graph_fleet(self, group, me):
        found, trusted, waiting = self.confirmed(group)
        members = {m['member_id']: m for m in group.get('members', []) if m.get('member_id')}
        if me['member_id'] in trusted:
            for mid in waiting:
                if mid != me['member_id'] and ft.keys_of(members[mid]):
                    self._prompt('member', group, '', members[mid])
            self._drop_prompts(lambda p: p['kind'] == 'member' and p['fleet'] == group['id'] and (p['member'] in found or p['member'] not in members))
            self._graph_keys_round(group, me, found)
            self._optout_prompts(group, me, found, trusted, members)
        if me['member_id'] not in found:
            return
        self._graph_ask_round(group, me, found)
        if self.graph_off(group, trusted) == 'off':
            self.set_meta('graph_off_ok:' + group['id'], (self.meta('graph_off:' + group['id']) or {}).get('at'))
            # an admin agreed: nothing new goes; what was shared before stays with the Fleet
        else:
            self.graph_publish(group, me, found)
        self._graph_want_round(group, me, found)  # catching up: with what is left of this hour's budget
        try:
            self._graph_serve(group, me, found, trusted)
        except Exception as error:  # noqa: BLE001 - the Network's own limit, or away: the rest next round
            LOG.info('shared graph catch-up paused fleet=%s error=%s', group['id'], type(error).__name__)

    # -- catching up, Machine to Machine
    def _graph_want_round(self, group, me, found):
        """This Machine asks its teammates for what it missed: once it is first confirmed, and when it was away longer than
        the Network keeps anything (KEEP_DAYS). A sealed 'want' says which snapshot it has of each teammate and how far
        back it got their threads; it goes again as that moves, for WANT_DAYS, then a last empty one ends it. Threads from
        before its last fetch it has already (since), less a day."""
        fleet = group['id']
        others = sorted(set(found) - {me['member_id']})
        if not others or self._budget() <= 0:
            return
        want = self.meta('graph_want:' + fleet) or {}
        fetched = self.meta('graph_fetched_at')
        if not self.meta('graph_confirmed:' + fleet) or not fetched or fetched < _iso_ago(KEEP_DAYS - 2):
            if not want or want.get('at', '') < _iso_ago(1):
                since = fetched if self.meta('graph_confirmed:' + fleet) and fetched else ''
                want = {'at': now(), 'got': {}, 'since': since and (dt.datetime.fromisoformat(since) - dt.timedelta(days=1)).isoformat()}
            self.set_meta('graph_confirmed:' + fleet, now())
        if not want:
            return
        tag = keyed(self.graph_secret(), 'want|' + fleet)
        if want['at'] < _iso_ago(WANT_DAYS):
            if not want.get('closed'):
                self._put(group, me, 'want', None, others, tag=tag)
                want['closed'] = True
                self.set_meta('graph_want:' + fleet, want)
            return
        with self.connect() as db:
            ensure(db)
            snap = {r['member']: r['seq'] for r in db.execute('SELECT member,seq FROM team_sources WHERE fleet=?', (fleet,))}
        body = {'at': want['at'], 'snap': snap, 'got': want.get('got') or {}, 'since': want.get('since') or ''}
        sent = hashlib.sha256(canon([body, others])).hexdigest()
        if want.get('sent') != sent:
            self._put(group, me, 'want', body, others, tag=tag)
            want['sent'] = sent
            self.set_meta('graph_want:' + fleet, want)

    def _graph_serve(self, group, me, found, trusted):
        """Passes on what a teammate who asked missed, from this Machine's own copies (team_blobs): each publisher's last
        snapshot and the deltas since first, then their threads and files, the newest first, a batch a round. Each blob
        goes exactly as its publisher signed it, its key sealed again for the one who asked. What someone who left
        shared goes only from an admin's Machine, which vouches for the key it was signed with."""
        fleet = group['id']
        members = {m.get('member_id') for m in group.get('members', [])}
        with self.connect() as db:
            ensure(db)
            wants = db.execute('SELECT member,body FROM team_wants WHERE fleet=?', (fleet,)).fetchall()
            signs = {r['member']: r['sign'] for r in db.execute('SELECT member,sign FROM team_sources WHERE fleet=?', (fleet,))}
        for want in wants:
            who = want['member']
            body = json.loads(want['body'])
            keys = self._public(group, who)
            if who == me['member_id'] or who not in found or not keys or str(body.get('at') or '') < _iso_ago(WANT_DAYS):
                continue
            budget = min(self._budget(), SERVE_BATCH)
            if budget <= 0:
                return
            served = self.meta(f'graph_served:{fleet}:{who}') or {}
            if served.get('at') != body['at']:
                served = {'at': body['at'], 'snap': {}, 'low': {}}
            with self.connect() as db:
                rows = [dict(r) for r in db.execute('SELECT * FROM team_blobs WHERE fleet=? AND member!=? ORDER BY seq DESC', (fleet, who))]
            sign_of = lambda p: (nc.public_keys(self._private())['sign'] if p == me['member_id'] else
                                 (self._public(group, p) or {}).get('sign') if p in found else signs.get(p))
            allowed = {r['member'] for r in rows if (r['member'] in found or (r['member'] not in members and me['member_id'] in trusted))
                       and sign_of(r['member'])}
            out = []
            for p in sorted(allowed):  # the records: the last snapshot and the deltas since, or only the deltas it lacks
                snaps = [r for r in rows if r['member'] == p and r['kind'] == 'snapshot']
                have = max(int((body.get('snap') or {}).get(p) or 0), int(served['snap'].get(p) or 0))
                if not snaps:
                    continue
                deltas = sorted((r for r in rows if r['member'] == p and r['kind'] == 'delta' and r['seq'] > max(have, snaps[0]['seq'])), key=lambda r: r['seq'])
                chain = ([snaps[0]] if snaps[0]['seq'] > have else []) + deltas
                if chain:
                    out.append(('snap', p, chain))
            low = lambda p: min(int((body.get('got') or {}).get(p) or 1 << 62), int(served['low'].get(p) or 1 << 62))
            since = str(body.get('since') or '')
            threads = [r for r in rows if r['member'] in allowed and r['kind'] in ('mail', 'file') and r['seq'] < low(r['member'])
                       and str(json.loads(r['header']).get('at') or '') >= since]
            out += [('low', r['member'], [r]) for r in threads]  # the newest first
            for what, p, chain in out:
                if budget <= 0:
                    break
                # A day's deltas can exceed one round. Send an ordered prefix and remember its last sequence,
                # so the next round continues that chain rather than waiting forever for a larger budget.
                chain = chain[:budget]
                for r in chain:
                    header = json.loads(r['header'])
                    wrap = nc.seal({'dk': r['dk'], 'h': r['sha'], 'from_key': sign_of(p)}, self._private(), keys, wrap_context(header, me['member_id'], who))
                    self.relay.call('POST', '/graph', {'op': 'relay', 'fleet': fleet, 'blob': {'header': header, 'ct': r['ct'], 'sig': r['sig']},
                                                       'wraps': {who: wrap}}, timeout=120 if r['kind'] == 'file' else 60)
                    self._spent()
                    budget -= 1
                if what == 'snap':
                    served['snap'][p] = chain[-1]['seq']
                else:
                    served['low'][p] = chain[0]['seq']
                self.set_meta(f'graph_served:{fleet}:{who}', served)

    def _graph_keys_round(self, group, me, found):
        """A confirmed admin's Machine: make the first graph key, seal it for members who don't have it, and make a new
        one when someone it was sealed for is gone."""
        fleet = group['id']
        keys = self.graph_keys(fleet)
        kv, key = self.graph_key(fleet)
        others = sorted(set(found) - {me['member_id']})
        sent = set(keys.get(kv, {}).get('to', [])) if kv else set()
        if not kv or (sent - set(found) - {me['member_id']}):
            kv, key = secrets.token_hex(8), nc.b64(secrets.token_bytes(32))
            keys[kv] = {'key': key, 'at': now(), 'by': me['member_id'], 'to': []}
            sent = set()
        missing = [m for m in others if m not in sent]
        if missing or not keys[kv].get('to'):
            header = self._put(group, me, 'key', {'kv': kv, 'key': key}, others) if others else None
            keys[kv]['to'] = sorted(set(keys[kv].get('to', [])) | set(header['to'] if header else []))
        self.set_meta('graph_keys:' + fleet, keys)

    def graph_publish(self, group, me, found):
        """Publishes what changed since the last round: a snapshot when the recipients or the key changed, or daily;
        else a delta. Then each client thread that changed as its own mail blob, with its attachments as file blobs,
        the newest first and within this Machine's budget: share_out keeps what went, so the next round goes on from
        there (a long history takes a few days). Someone who joins later gets what went before by catching up."""
        fleet = group['id']
        kv, key = self.graph_key(fleet)
        if not kv:
            return
        to = sorted(set(found) - {me['member_id']})
        if not to:
            return
        with self.connect() as db:
            ensure(db)
            scope = Scope(db, self.fleet_accounts(fleet), self.internal(fleet), self.my_emails(), self._personal_people(db))
            others = [k['key'] for kv_, k in sorted(self.graph_keys(fleet).items(), key=lambda x: x[1]['at'], reverse=True) if kv_ != kv][:3]
            records, threads, stats, files = collect(db, scope, [key, *others], self.graph_secret())
            before = {(r['kind'], r['key']): r['hash'] for r in db.execute('SELECT kind,key,hash FROM share_out WHERE fleet=?', (fleet,))}
        state = self.meta('graph_out:' + fleet, {}) or {}
        hashes = {rid: hashlib.sha256(canon(r)).hexdigest() for rid, r in records.items()}
        thread_hashes = {tag: hashlib.sha256(canon(t)).hexdigest() for tag, t in threads.items()}  # someone new gets them by catching up
        new_people = state.get('to') != to or state.get('kv') != kv
        snapshot = new_people or not state.get('snapshot_at') or time.time() - state.get('snapshot_at', 0) > SNAPSHOT_EVERY
        changed = [rid for rid, h in hashes.items() if before.get(('record', rid)) != h]
        removed = [k for (kind, k) in before if kind == 'record' and k not in hashes]
        if self._budget() <= 0:
            return
        if snapshot:
            header = self._put(group, me, 'snapshot', {'kv': kv, 'records': list(records.values()), 'threads': sorted(threads)}, to, kv=kv)
            if header:
                state.update(snapshot_at=time.time(), to=to, kv=kv, last=header['seq'])
        elif changed or removed or set(threads) != set(state.get('threads') or []):
            header = self._put(group, me, 'delta', {'kv': kv, 'prev': state.get('last'), 'records': [records[r] for r in changed], 'removed': removed,
                                                     'threads': sorted(threads)}, to, kv=kv)
            if header:
                state['last'] = header['seq']
        else:
            header = True
        if not header:
            return
        state['threads'] = sorted(threads)
        with self.connect() as db:
            db.execute("DELETE FROM share_out WHERE fleet=? AND kind='record'", (fleet,))
            db.executemany('INSERT INTO share_out(fleet,kind,key,hash,at) VALUES(?,?,?,?,?)', [(fleet, 'record', k, h, now()) for k, h in hashes.items()])
        self.set_meta('graph_out:' + fleet, state)
        budget = self._budget()
        try:
            for tag in [k for (kind, k) in before if kind == 'thread' and k not in threads]:  # no longer shared: gone from the team's Machines
                if budget <= 0:
                    break
                budget -= self._unshare(group, me, to, tag, before)
            for tag, body in threads.items():  # the newest first
                if budget <= 0:
                    break
                if before.get(('thread', tag)) == thread_hashes[tag]:
                    continue
                body, used = self._with_files(group, me, to, tag, body, files.get(tag) or [], before, budget - 1)
                if body is None:
                    break  # the budget is spent halfway through its files: the rest next round
                self._put(group, me, 'mail', body, to, tag=tag)
                budget -= used + 1
                with self.connect() as db:
                    db.execute('INSERT OR REPLACE INTO share_out(fleet,kind,key,hash,at) VALUES(?,?,?,?,?)', (fleet, 'thread', tag, thread_hashes[tag], now()))
        except Exception as error:  # noqa: BLE001 - the Network's own limit, or away: what went is kept, the rest next round
            LOG.info('shared graph threads paused fleet=%s error=%s', fleet, type(error).__name__)
        with self.connect() as db:
            sent = {r['key'] for r in db.execute("SELECT key FROM share_out WHERE fleet=? AND kind='thread'", (fleet,))}
        stats['waiting'] = sum(1 for t in threads if t not in sent)
        self.set_meta('graph_week:' + fleet, {**stats, 'at': now()})

    def _unshare(self, group, me, to, tag, before):
        """A thread no longer shared: an empty blob for it and each of its files. Returns how many blobs went."""
        fleet, used = group['id'], 0
        for (kind, ftag), h in before.items():
            if kind == 'file' and h.split(':', 1)[0] == tag:
                self._put(group, me, 'file', None, to, tag=ftag)
                used += 1
                with self.connect() as db:
                    db.execute("DELETE FROM share_out WHERE fleet=? AND kind='file' AND key=?", (fleet, ftag))
        self._put(group, me, 'mail', None, to, tag=tag)
        with self.connect() as db:
            db.execute("DELETE FROM share_out WHERE fleet=? AND kind='thread' AND key=?", (fleet, tag))
        return used + 1

    def _with_files(self, group, me, to, tag, body, sources, before, budget):
        """The thread's messages with their attachments: each file up to MAX_FILE goes as its own sealed blob (once per
        recipients), bigger ones are listed as too large to share. Returns (body, file blobs sent), or (None, n) when
        the budget ran out first. Read-only from the mailbox, and only for a whole client thread. Each file goes once:
        someone who joins later gets it by catching up."""
        fetch = getattr(self, 'mail_files', None)
        used = 0
        if not fetch or body.get('private'):
            return body, 0
        body = {**body, 'messages': [dict(m) for m in body['messages']]}
        for message, (event_id, _account, _kind) in zip(body['messages'], sources):
            try:
                listed = fetch(event_id) or []
            except Exception as error:  # noqa: BLE001 - retry this unfinished thread when the mailbox is back
                LOG.info('shared graph attachments unavailable error=%s', type(error).__name__)
                # Do not record the unchanged thread as fully shared: without another email, the listing would
                # never be tried again. Already-sent files remain in share_out and aren't downloaded twice.
                raise Later('shared attachments are temporarily unavailable') from None
            out = []
            for a in listed[:20]:
                if not isinstance(a, dict) or a.get('inline') or a.get('kind', 'file') != 'file' or not a.get('id'):
                    continue
                entry = {'name': str(a.get('name') or 'attachment')[:120], 'size': int(a.get('size') or 0), 'mime': str(a.get('mime') or '')[:100]}
                if security_levels.fleet_level(group) == 'careful':  # a Careful Fleet shares no attachments
                    out.append({**entry, 'not_shared': True})
                    continue
                if entry['size'] > MAX_FILE:
                    out.append({**entry, 'too_large': True})
                    continue
                ftag = keyed(self.graph_secret(), f'file|{tag}|{event_id}|{a["id"]}')
                if before.get(('file', ftag)) != tag:
                    if used >= budget:
                        return None, used
                    data = fetch(event_id, a['id'])
                    if not isinstance(data, bytes) or len(data) > MAX_FILE:
                        out.append({**entry, 'too_large': True})
                        continue
                    self._put(group, me, 'file', {'name': entry['name'], 'mime': entry['mime'], 'data': nc.b64(data)}, to, tag=ftag)
                    used += 1
                    with self.connect() as db:
                        db.execute('INSERT OR REPLACE INTO share_out(fleet,kind,key,hash,at) VALUES(?,?,?,?,?)', (group['id'], 'file', ftag, tag, now()))
                    entry['size'] = len(data)
                out.append({**entry, 'file': ftag})
            message['attachments'] = out
        return body, used

    # -- receiving
    def graph_fetch(self):
        with self.connect() as db:
            ensure(db)
            waiting = db.execute('SELECT id,item,at FROM team_pending ORDER BY at').fetchall()
        expired = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=PENDING_DAYS)).isoformat()
        for row in waiting:
            try:
                self.graph_take(json.loads(row['item']))
            except Later:
                if row['at'] >= expired:
                    continue
            except (nc.SealError, ValueError, KeyError, TypeError):
                pass
            with self.connect() as db:
                db.execute('DELETE FROM team_pending WHERE id=?', (row['id'],))
        cursor = int(self.meta('graph_cursor', 0) or 0)
        for _ in range(20):
            found = self.relay.call('GET', f'/graph?after={cursor}&limit=50', timeout=60)
            taken = []
            for item in found.get('items') or []:
                try:
                    self.graph_take(item)
                except Later:
                    with self.connect() as db:
                        db.execute('INSERT OR IGNORE INTO team_pending(id,item,at) VALUES(?,?,?)',
                                   (str((item.get('header') or {}).get('id')) + ':' + str(item.get('by')), json.dumps(item), now()))
                except (nc.SealError, ValueError, KeyError, TypeError) as error:  # a catch-up from two teammates: one is enough
                    relayed = item.get('by') not in (None, (item.get('header') or {}).get('from'))
                    LOG.log(logging.INFO if relayed else logging.WARNING, 'shared graph blob dropped member=%s by=%s reason=%s',
                            (item.get('header') or {}).get('from'), item.get('by'), error)
                cursor = max(cursor, int(item.get('cursor') or 0))
                if HEX.fullmatch(str((item.get('header') or {}).get('id'))):
                    taken.append(item['header']['id'])
            self.set_meta('graph_cursor', cursor)
            if taken:  # kept here now (or waiting here): the Network deletes its copy once every Machine took it
                self.relay.call('POST', '/graph', {'op': 'ack', 'ids': taken}, timeout=60)
            if not found.get('more'):
                break
        self.set_meta('graph_fetched_at', now())

    def graph_take(self, item):
        """One blob, taken only when everything checks out (module docstring): sealed for this Machine by its publisher,
        or passed on by a confirmed teammate's Machine because this one missed it, still signed by its publisher."""
        header = item.get('header') or {}
        group = next((g for g in (self.directory().get('fleets') or []) if g.get('id') == header.get('fleet') and g.get('kind') == 'work'), None)
        me = group and self._me_in(group)
        if not me:
            raise ValueError('not a Work Fleet of this Machine')
        found, trusted, _ = self.confirmed(group)
        members = {m.get('member_id') for m in group.get('members', [])}
        sender, by = header.get('from'), item.get('by') or header.get('from')
        if by == sender and me['member_id'] in (header.get('to') or []):
            with self.connect() as db:
                ensure(db)
                source = db.execute('SELECT * FROM team_sources WHERE fleet=? AND member=?', (group['id'], sender)).fetchone()
            if sender in found:
                keys = self._public(group, sender)
            elif source and source['left_at'] and str(header.get('at') or '') < source['left_at'] and source['sign']:
                keys = self._pinned(sender)
            elif sender in members:
                raise Later('not from a confirmed member yet')
            else:
                raise ValueError('not from a confirmed member')
            if not keys:
                raise ValueError('no keys for the sender')
            opened = open_blob(item, self._private(), me['member_id'], keys, keys['sign'])
        else:  # passed on: a catch-up
            if header.get('kind') not in RELAYED:
                raise ValueError('only records, threads and files are passed on')
            if by not in found:
                if by in members:
                    raise Later('passed on by a teammate not confirmed yet')
                raise ValueError('passed on by someone who is not a confirmed member')
            if sender in found:
                sign = (self._public(group, sender) or {}).get('sign')
                if not sign:
                    raise ValueError('no keys for the sender')
            elif sender in members:
                raise Later('not from a confirmed member yet')
            elif by in trusted:
                sign = (self._pinned(sender) or {}).get('sign')  # else the key the admin vouches for
            else:
                raise ValueError('what someone who left shared is passed on only by a confirmed admin')
            opened = open_blob(item, self._private(), me['member_id'], self._public(group, by), sign)
            if header.get('kind') in ('mail', 'file'):
                self._caught_up(group['id'], sender, int(header['seq']))
        header, body, dk = opened
        if header['kind'] == 'key' and sender not in trusted:
            raise ValueError('a graph key from someone who is not a confirmed admin')
        if header['kind'] == 'want' and sender not in found:
            raise ValueError('a want from someone who is not a confirmed member')
        self._apply(group, header, body, dk, item)

    def _caught_up(self, fleet, sender, seq):
        """How far back this Machine's want got a teammate's threads: they go on below that."""
        want = self.meta('graph_want:' + fleet) or {}
        if want and not want.get('closed'):
            got = want.setdefault('got', {})
            got[sender] = min(int(got.get(sender) or seq), seq)
            self.set_meta('graph_want:' + fleet, want)

    def _pinned(self, mid):
        with self.connect() as db:
            row = db.execute('SELECT keys FROM network_pins WHERE member_id=?', (mid,)).fetchone()
        return json.loads(row['keys']) if row else None

    def _apply(self, group, header, body, dk, item):
        fleet, sender, kind, seq = group['id'], header['from'], header['kind'], int(header['seq'])
        name = next((str(m.get('label') or m.get('name') or '') for m in group.get('members', []) if m.get('member_id') == sender), '')
        if not isinstance(body, dict):
            raise ValueError('not a blob body')
        with self.connect() as db:
            ensure(db)
            row = db.execute('SELECT * FROM team_sources WHERE fleet=? AND member=?', (fleet, sender)).fetchone()
            if not row:
                sign = (self._public(group, sender) or {}).get('sign')
                db.execute('INSERT INTO team_sources(fleet,member,name,sign,at) VALUES(?,?,?,?,?)', (fleet, sender, name[:80], sign, now()))
                row = db.execute('SELECT * FROM team_sources WHERE fleet=? AND member=?', (fleet, sender)).fetchone()
            elif name and name != row['name']:
                db.execute('UPDATE team_sources SET name=? WHERE fleet=? AND member=?', (name[:80], fleet, sender))
            if kind in ('mail', 'file'):
                # A tombstone removes the visible row, but must still outrank an old copy relayed later.
                latest = db.execute('SELECT MAX(seq) FROM team_blobs WHERE fleet=? AND member=? AND kind=? AND tag=?',
                                    (fleet, sender, kind, header.get('tag'))).fetchone()[0]
                if latest is not None and seq <= latest:
                    raise ValueError('older mail or file')
            keep_blob(db, header, item['ct'], item['sig'], dk)
            if kind == 'key':
                kv, key = body.get('kv'), body.get('key')
                if isinstance(kv, str) and re.fullmatch(r'[a-f0-9]{16}', kv) and isinstance(key, str):
                    nc.unb64(key, 32)
                    row = db.execute('SELECT value FROM fleet_meta WHERE key=?', ('graph_keys:' + fleet,)).fetchone()
                    keys = (json.loads(row[0]) if row else None) or {}
                    if kv not in keys:  # the same connection: set_meta's own would wait for this one
                        keys[kv] = {'key': key, 'at': header['at'], 'by': sender, 'to': header.get('to', [])}
                        db.execute('INSERT OR REPLACE INTO fleet_meta(key,value) VALUES(?,?)', ('graph_keys:' + fleet, json.dumps(keys)))
                return
            if kind == 'snapshot':
                if seq <= row['snapshot']:
                    raise ValueError('an older snapshot')
                db.execute('DELETE FROM team_records WHERE fleet=? AND member=?', (fleet, sender))
                self._records(db, fleet, sender, body.get('records'), header)
                db.execute('UPDATE team_sources SET snapshot=?,seq=?,threads=?,at=? WHERE fleet=? AND member=?',
                           (seq, seq, json.dumps(self._tags(body)), now(), fleet, sender))
                db.execute('DELETE FROM team_mail WHERE fleet=? AND member=? AND tag NOT IN (SELECT value FROM json_each(?))',
                           (fleet, sender, json.dumps(self._tags(body))))
                db.execute("DELETE FROM team_blobs WHERE fleet=? AND member=? AND kind='mail' AND tag NOT IN (SELECT value FROM json_each(?))",
                           (fleet, sender, json.dumps(self._tags(body))))
                listed = {a.get('file') for r in db.execute('SELECT body FROM team_mail WHERE fleet=? AND member=?', (fleet, sender))
                          for m in json.loads(r['body']).get('messages') or [] for a in m.get('attachments') or []}
                for r in db.execute('SELECT tag FROM team_files WHERE fleet=? AND member=?', (fleet, sender)).fetchall():
                    if r['tag'] not in listed:
                        db.execute('DELETE FROM team_files WHERE fleet=? AND member=? AND tag=?', (fleet, sender, r['tag']))
                        db.execute("DELETE FROM team_blobs WHERE fleet=? AND member=? AND kind='file' AND tag=?", (fleet, sender, r['tag']))
            elif kind == 'delta':
                if body.get('prev') != row['seq'] or seq <= row['seq']:
                    raise ValueError('a delta out of order: waits for the next snapshot')
                self._records(db, fleet, sender, body.get('records'), header)
                for rid in body.get('removed') or []:
                    db.execute('DELETE FROM team_records WHERE fleet=? AND member=? AND rid=?', (fleet, sender, str(rid)))
                db.execute('UPDATE team_sources SET seq=?,threads=?,at=? WHERE fleet=? AND member=?', (seq, json.dumps(self._tags(body)), now(), fleet, sender))
                db.execute('DELETE FROM team_mail WHERE fleet=? AND member=? AND tag NOT IN (SELECT value FROM json_each(?))',
                           (fleet, sender, json.dumps(self._tags(body))))
            elif kind == 'mail':
                tag = header.get('tag')
                if not HEX.fullmatch(str(tag)):
                    raise ValueError('mail without a tag')
                old = db.execute('SELECT seq FROM team_mail WHERE fleet=? AND member=? AND tag=?', (fleet, sender, tag)).fetchone()
                if old and old['seq'] >= seq:
                    raise ValueError('older mail')
                if not body:
                    db.execute('DELETE FROM team_mail WHERE fleet=? AND member=? AND tag=?', (fleet, sender, tag))
                else:
                    db.execute('INSERT OR REPLACE INTO team_mail(fleet,member,tag,seq,body,at) VALUES(?,?,?,?,?,?)',
                               (fleet, sender, tag, seq, json.dumps(_clean_mail(body, tag)), header['at']))
            elif kind == 'file':
                tag = header.get('tag')
                if not HEX.fullmatch(str(tag)):
                    raise ValueError('a file without a tag')
                old = db.execute('SELECT seq FROM team_files WHERE fleet=? AND member=? AND tag=?', (fleet, sender, tag)).fetchone()
                if old and old['seq'] >= seq:
                    raise ValueError('an older file')
                if not body:
                    db.execute('DELETE FROM team_files WHERE fleet=? AND member=? AND tag=?', (fleet, sender, tag))
                else:
                    data = nc.unb64(body.get('data'))
                    if len(data) > MAX_FILE:
                        raise ValueError('a file over the limit')
                    db.execute('INSERT OR REPLACE INTO team_files(fleet,member,tag,seq,name,mime,size,data,at) VALUES(?,?,?,?,?,?,?,?,?)',
                               (fleet, sender, tag, seq, _clean_text(body.get('name'), 120) or 'attachment', _mime(body.get('mime')), len(data),
                                nc.b64(data), header['at']))
            elif kind == 'want':  # a teammate's Machine asks for what it missed (_graph_serve)
                old = db.execute('SELECT seq FROM team_wants WHERE fleet=? AND member=?', (fleet, sender)).fetchone()
                if old and old['seq'] >= seq:
                    raise ValueError('an older want')
                if not body:
                    db.execute('DELETE FROM team_wants WHERE fleet=? AND member=?', (fleet, sender))
                    return
                at = _clean_text(body.get('at'), 40)
                counts = lambda d: {str(k)[:40]: int(v) for k, v in list(d.items())[:500] if type(v) is int} if isinstance(d, dict) else {}
                if not at:
                    raise ValueError('a want without its time')
                db.execute('INSERT OR REPLACE INTO team_wants(fleet,member,body,seq,at) VALUES(?,?,?,?,?)',
                           (fleet, sender, json.dumps({'at': at, 'snap': counts(body.get('snap')), 'got': counts(body.get('got')),
                                                       'since': _clean_text(body.get('since'), 40) or ''}), seq, now()))
            elif kind == 'ask':  # a member asks to stop sharing their Work (or not any more): for the admins' OK
                old = db.execute('SELECT seq FROM team_asks WHERE fleet=? AND member=?', (fleet, sender)).fetchone()
                if old and old['seq'] >= seq:
                    raise ValueError('an older ask')
                asked = _clean_text(body.get('asked'), 25)
                if not asked:
                    raise ValueError('an ask without its time')
                db.execute('INSERT OR REPLACE INTO team_asks(fleet,member,off,asked,seq,at) VALUES(?,?,?,?,?,?)',
                           (fleet, sender, int(body.get('off') is True), asked, seq, now()))

    @staticmethod
    def _tags(body):
        return [t for t in body.get('threads') or [] if isinstance(t, str) and HEX.fullmatch(t)][:MAX_THREADS]

    def _records(self, db, fleet, sender, records, header):
        for r in (records or [])[:MAX_RECORDS]:
            if not isinstance(r, dict) or r.get('t') not in ('person', 'org', 'project') or not HEX.fullmatch(str(r.get('r'))):
                continue
            r['kv'] = header.get('kv')
            db.execute('INSERT OR REPLACE INTO team_records(fleet,member,rid,kind,body,at) VALUES(?,?,?,?,?,?)',
                       (fleet, sender, r['r'], r['t'], json.dumps(_clean_record(r)), header['at']))

    def _graph_forget(self, groups):
        """A Fleet this Machine is no longer in: what it got from it goes. A teammate who left: kept (the Fleet's), marked."""
        ids = {g['id'] for g in groups if self._me_in(g)}
        with self.connect() as db:
            ensure(db)
            for (fleet,) in db.execute('SELECT DISTINCT fleet FROM team_sources').fetchall():
                if fleet not in ids:
                    for table in ('team_records', 'team_mail', 'team_files', 'team_asks', 'team_sources', 'team_blobs', 'team_wants', 'share_out'):
                        db.execute(f'DELETE FROM {table} WHERE fleet=?', (fleet,))
                    for key in ('graph_keys:', 'graph_out:', 'graph_members:', 'graph_off:', 'graph_off_ok:', 'graph_ask_sent:', 'graph_week:',
                                'graph_want:', 'graph_confirmed:'):
                        db.execute('DELETE FROM fleet_meta WHERE key=?', (key + fleet,))
                    db.execute('DELETE FROM fleet_meta WHERE key LIKE ?', ('graph_served:' + fleet + ':%',))
            for group in groups:
                now_in = {m.get('member_id') for m in group.get('members', [])}
                for row in db.execute('SELECT member FROM team_sources WHERE fleet=? AND left_at IS NULL', (group['id'],)).fetchall():
                    if row['member'] not in now_in:
                        db.execute('UPDATE team_sources SET left_at=? WHERE fleet=? AND member=?', (now(), group['id'], row['member']))

    # -- the owner's controls
    def graph_unjudged(self, limit=120):
        """Work client threads still to be judged for each Work Fleet this Machine shares with (confirmed, not off)."""
        groups = []
        for group in self._kept_groups():
            me = self._me_in(group)
            if me:
                found, trusted, _ = self.confirmed(group)
                if me['member_id'] in found and self.graph_off(group, trusted) != 'off':
                    groups.append(group)
        out, skip = [], set()
        with self.connect() as db:
            ensure(db)
            for group in groups:
                for item in unjudged(db, Scope(db, self.fleet_accounts(group['id']), self.internal(group['id']), self.my_emails()), limit - len(out)):
                    if item['event_id'] not in skip:
                        skip.add(item['event_id'])
                        out.append(item)
        return out[:limit]

    def graph_off(self, group, trusted=None):
        """This Machine's sharing with one Work Fleet: None (on), 'asked' (asked an admin to stop it; it stays on until
        one agrees) or 'off' (an admin agreed to that very ask). Like in any CRM, nobody stops on their own."""
        wish = self.meta('graph_off:' + group['id'])
        if not wish:
            return None
        if self.meta('graph_off_ok:' + group['id']) == wish.get('at'):
            return 'off'  # kept once seen: an admin who agreed and left since doesn't turn it back on
        if trusted is None:
            trusted, _ = self.trust(group)
        me = self._me_in(group)
        agreed = ft.optouts(group, trusted).get(me['member_id']) if me else None
        return 'off' if agreed and agreed.get('asked') == wish.get('at') else 'asked'

    def graph_optout(self, fleet, off=True):
        """Ask the admins to stop sharing this Machine's Work with the team (off), or share again (at once, no OK
        needed). Keep private on a record works any time, without asking."""
        group = next((g for g in (self.directory().get('fleets') or []) if g.get('id') == fleet and g.get('kind') == 'work'), None)
        if not group or not self._me_in(group):
            raise ValueError('Not a Work Fleet of yours')
        if off:
            if not self.meta('graph_off:' + fleet):
                self.set_meta('graph_off:' + fleet, {'at': now()})
        else:
            self.set_meta('graph_off:' + fleet, None)
            self.set_meta('graph_off_ok:' + fleet, None)
        try:
            self.graph_round(force=True)
        except (ValueError, OSError, KeyError, TypeError) as error:
            LOG.info('shared graph after an opt-out change error=%s', type(error).__name__)
        return {'ok': True, 'off': self.graph_off(group)}

    def _graph_ask_round(self, group, me, found):
        """The member's ask (or that it is withdrawn) reaches the admins' Machines, sealed like everything else; sent
        again when the Fleet's members change."""
        wish = self.meta('graph_off:' + group['id']) or {}
        to = sorted(set(found) - {me['member_id']})
        want = {'off': bool(wish), 'asked': wish.get('at') or now(), 'to': to}
        sent = self.meta('graph_ask_sent:' + group['id'])
        if not to or (not sent and not wish) or (sent and sent.get('off') == want['off'] and sent.get('to') == to
                                                 and (not wish or sent.get('asked') == wish.get('at'))):
            return
        if self._budget() <= 0:
            return
        if self._put(group, me, 'ask', {'off': want['off'], 'asked': want['asked']}, to, tag=keyed(self.graph_secret(), 'ask')):
            self.set_meta('graph_ask_sent:' + group['id'], want)

    def _optout_prompts(self, group, me, found, trusted, members):
        """A confirmed admin's Machine: a member's standing ask to stop sharing becomes an OK for its owner, on the
        Fleet tab. Gone once an admin agreed, the member withdrew it, or they left."""
        with self.connect() as db:
            ensure(db)
            asks = {r['member']: dict(r) for r in db.execute('SELECT * FROM team_asks WHERE fleet=?', (group['id'],))}
        agreed = ft.optouts(group, trusted)
        declined = self.meta('optout_declined', []) or []
        standing = {mid: a for mid, a in asks.items() if a['off'] and mid in found and mid != me['member_id'] and mid in members
                    and (agreed.get(mid) or {}).get('asked') != a['asked'] and [group['id'], mid, a['asked']] not in declined}
        self._drop_prompts(lambda p: p['kind'] == 'optout' and p['fleet'] == group['id'] and (p['member'] not in standing or p.get('asked') != standing[p['member']]['asked']))
        for mid, a in standing.items():
            self._prompt('optout', group, '', members[mid], asked=a['asked'])

    def optout_ok(self, prompt, group, me, member, keys):
        """The admin's owner agreed: a signed statement for that member's key and that very ask."""
        item = ft.sign(self._private(), ft.optout(group['id'], member['member_id'], keys['sign'], me['member_id'], prompt.get('asked'), now()))
        self.relay.call('POST', '/trust', {'fleet': group['id'], 'item': item}, timeout=30)
        self.directory(refresh=True)
        return {'ok': True, 'done': True}

    # -- the team's view
    def team_sources(self):
        with self.connect() as db:
            ensure(db)
            return {(r['fleet'], r['member']): dict(r) for r in db.execute('SELECT * FROM team_sources')}

    def team_items(self, db, kind):
        """Teammates' records of one kind, as [(fleet, member, record)]."""
        ensure(db)
        return [(r['fleet'], r['member'], json.loads(r['body'])) for r in db.execute('SELECT * FROM team_records WHERE kind=?', (kind,))]

    def hash_index(self, db):
        """{hash: local node id} for this Machine's own people (by address) and organisations (by domain), with every
        graph key it holds."""
        index = {}
        keys = [k['key'] for ref in self.work_fleets() for k in self.graph_keys(ref).values()]
        if not keys:
            return index
        for row in db.execute("SELECT id,kind,info FROM graph_nodes WHERE kind IN ('person','org')"):
            info = json.loads(row['info'] or '{}')
            values = (['email:' + i[6:].lower() for i in [row['id'], *info.get('identifiers', [])] if str(i).startswith('email:')]
                      if row['kind'] == 'person' else (['domain:' + info['domain']] if info.get('domain') else []))
            for key in keys:
                for v in values:
                    index.setdefault(keyed(key, v), row['id'])
        return index

    def team_graph(self, db, kinds=('person', 'org', 'project')):
        """The team's entities: [{key, kind, local (node id or None), sources: [(fleet, member, record)]}], matched by
        hashed address, hashed domain, or board and name."""
        index = self.hash_index(db)
        names = {(f, m): (s.get('name') or '', bool(s.get('left_at'))) for (f, m), s in self.team_sources().items()}
        hidden = {'project:' + str(i) for i in graph.hidden_project_ids(db)}  # a teammate's project never lands on an Only-you one
        local_projects = {row['name'].casefold(): row['id'] for row in db.execute("SELECT id,name FROM graph_nodes WHERE kind='project'") if row['id'] not in hidden}
        groups, by_hash = {}, {}
        for kind in kinds:
            for fleet, member, r in self.team_items(db, kind):
                if kind == 'person':
                    hashes = list(r.get('ids') or [])
                elif kind == 'org':
                    hashes = r.get('d') if isinstance(r.get('d'), list) else [r['d']] if r.get('d') else []
                else:
                    hashes = ['project:' + str(r.get('name') or '').casefold()]
                local = next((index[h] for h in hashes if h in index), None)
                if kind == 'project':
                    local = local_projects.get(str(r.get('name') or '').casefold())
                key = local or next((by_hash[h] for h in hashes if h in by_hash), None) or f'team:{kind}:{r["r"]}'
                for h in hashes:
                    by_hash.setdefault(h, key)
                entry = groups.setdefault(key, {'key': key, 'kind': kind, 'local': local, 'sources': []})
                entry['sources'].append({'fleet': fleet, 'member': member, 'name': names.get((fleet, member), ('', False))[0],
                                         'left': names.get((fleet, member), ('', False))[1], 'record': r})
        return list(groups.values())

    # -- the owner's view of it (dashboard, chat)
    def _kept_groups(self):
        """The Work Fleets from the directory this Machine keeps: no call, and no write while a caller holds the database."""
        return [g for g in ((self.meta('directory') or {}).get('fleets') or []) if g.get('kind') == 'work' and g.get('id')]

    def scopes(self, db):
        """{fleet: Scope} for each Work Fleet this Machine is in."""
        found = {}
        for group in self._kept_groups():
            if group.get('kind') == 'work' and group.get('id') and self._me_in(group):
                found[group['id']] = Scope(db, self.fleet_accounts(group['id']), self.internal(group['id']), self.my_emails(),
                                           self._personal_people(db))
        return found

    def _personal_people(self, db):
        """Graph people who are only in the owner's Family or Friends (graph.fleet_marks, from the directory this
        Machine keeps): the owner's own life, whatever address they write from. Never shared with a Work Fleet."""
        try:
            marks = graph.fleet_marks(db, self.fleets())
        except (ValueError, TypeError, KeyError, AttributeError):
            return set()
        return {node for node, mark in marks.items() if mark['in'] and all(x['kind'] in ('family', 'friends') for x in mark['in'])}

    def thread_labels(self, db, rows):
        """{event id: {'share': 'team' | 'only_you', 'fixed': bool, 'why'}} for messages in a Work Fleet's accounts;
        nothing for the rest (Personal). fixed: colleagues only, never shared, so no switch."""
        scopes = self.scopes(db)
        out = {}
        for r in rows:
            for scope in scopes.values():
                view = visibility(db, scope, {'id': r['id'], 'account_id': r['account_id'], 'kind': r['kind'], 'metadata': r['metadata']})
                if view is None:
                    continue
                hand = db.execute('SELECT hand FROM share_threads WHERE thread=?', (thread_of(r['kind'], r['metadata'] if isinstance(r['metadata'], dict)
                                                                                                else json.loads(r['metadata'] or '{}'), r['id']),)).fetchone()
                out[r['id']] = {'share': label(view) or 'only_you', 'fixed': view == 'internal',
                                'why': {'personal': 'Personal', 'internal': 'Only colleagues', 'line': 'Private: the team sees only that it exists', 'pending': 'Not looked at yet',
                                        'private': 'By hand' if hand and hand['hand'] == 'private' else 'Nobody outside', 'team': 'By hand' if hand and hand['hand'] == 'team' else 'Client'}[view]}
                break
        return out

    def is_private(self, db, node_id):
        ensure(db)
        return bool(db.execute('SELECT 1 FROM share_private WHERE node_id=?', (node_id,)).fetchone())

    def sharing(self):
        """Per Work Fleet: confirmed or waiting, whether this Machine's sharing is on, asked off or off, and this week's
        counts. For the dashboard."""
        out = []
        for group in self._kept_groups():
            me = self._me_in(group)
            if not me:
                continue
            found, trusted, waiting = self.confirmed(group)
            prompts = [{k: p.get(k) for k in ('id', 'name', 'email', 'fp')} for p in self._prompts() if p['kind'] == 'member' and p['fleet'] == group['id']]
            out.append({'fleet': group['id'], 'name': str(group.get('name') or '')[:80], 'confirmed': me['member_id'] in found,
                        'off': self.graph_off(group, trusted), 'week': self.meta('graph_week:' + group['id']),
                        'waiting': len([m for m in waiting if m != me['member_id']]), 'admin': me['member_id'] in trusted, 'prompts': prompts,
                        'teammates': len(set(found) - {me['member_id']})})  # 0: nothing goes until an admin OKs a teammate's Machine
        return out

    def _mine_shared(self, db):
        """Local node ids this Machine shares with a Fleet now (published), {node id: fleet}."""
        secret = self.graph_secret()
        out_keys = {r['key'] for r in db.execute("SELECT key FROM share_out WHERE kind='record'")}
        if not out_keys:
            return set()
        return {r['id'] for r in db.execute("SELECT id FROM graph_nodes") if keyed(secret, 'record|' + r['id']) in out_keys}

    def team_overview(self, db, mine):
        """The Team view: the owner's shared records and teammates' ones, merged, with 'via <name>'. mine: graph.overview."""
        ensure(db)
        entries = self.team_graph(db)
        shared = self._mine_shared(db) - {'project:' + str(i) for i in graph.hidden_project_ids(db)}
        local = {}
        for r in db.execute('SELECT * FROM graph_nodes WHERE id IN (SELECT value FROM json_each(?))',
                            (json.dumps(sorted(shared | {e['local'] for e in entries if e['local']})),)):
            local[r['id']] = graph.node_view(r)
        people, orgs, projects, seen = [], [], [], set()
        for e in entries:
            view = dict(local.get(e['local']) or {})
            rec = resolve(e, None)
            item = view or {'id': e['key'], 'kind': e['kind'], 'name': rec['name'] or '?', 'strength': round(rec['score'], 2), 'last_at': rec['last'],
                            'signals': 0, 'team_only': True, 'role': rec['role'], 'closeness': graph.closeness(rec['score'])}
            if e['kind'] == 'project' and not view:
                item.update(state='active', people=[])
            if e['kind'] == 'person' and not view and rec.get('org'):
                item['org_name'] = rec['org'].get('name')
            if view and '@' in str(item.get('name') or '') and rec.get('name') and '@' not in rec['name']:
                item['name'] = rec['name']  # only an address here; a teammate knows their name
                item['role'] = item.get('role') or rec.get('role')
            item['via'] = via(e['sources'])
            item['strength'] = max(item.get('strength') or 0, round(rec['score'], 2))
            seen.add(item['id'])
            {'person': people, 'org': orgs, 'project': projects}[e['kind']].append(item)
        for node_id, view in local.items():
            if node_id in seen or node_id not in shared:
                continue
            {'person': people, 'org': orgs, 'project': projects}.get(view['kind'], []).append(view)
        key = lambda n: -(n.get('strength') or 0)
        return {'team': True, 'people': sorted(people, key=key)[:60], 'orgs': sorted(orgs, key=key)[:30], 'projects': sorted(projects, key=key)[:40],
                'quiet': [], 'new': [], 'boards': self.team_boards(db, entries, shared), 'counts': {'person': len(people), 'org': len(orgs), 'project': len(projects)},
                'sharing': self.sharing()}

    def team_boards(self, db, entries, shared):
        """Shared Boards: the team's pipelines. A tile per project, with its owner (the closest), its stage (the newest
        move by hand, else the newest a message showed) and who moved it."""
        team_ids, hidden = graph.team_board_ids(db), {'project:' + str(i) for i in graph.hidden_project_ids(db)}
        mine = {b['name'].casefold(): b for b in graph.boards(db, now()) if b['id'] in team_ids}  # an Only-you board never shows here
        boards = {}
        for name, b in mine.items():
            projects = [dict(p, mine=True) for p in b['projects'] if p['id'] in shared and p['id'] not in hidden]
            boards[name] = {'name': b['name'], 'stages': b['stages'], 'projects': projects, 'stuck': 0, 'team': True}
        names = {(f, m): (s.get('name') or '') for (f, m), s in self.team_sources().items()}
        for e in entries:
            if e['kind'] != 'project':
                continue
            mine_tile = next((p for b in boards.values() for p in b['projects'] if p['id'] == e['local']), None)
            local_record = None
            if mine_tile:
                local_record = {'stage': mine_tile['stage'], 'stage_hand': mine_tile['by'] == 'owner', 'stage_at': mine_tile['at'], 'board': None,
                                'score': next((n['strength'] for n in [graph.node_view(r) for r in db.execute('SELECT * FROM graph_nodes WHERE id=?', (e['local'],))]), 0)}
            rec = resolve(e, local_record)
            board_name = next((s['record'].get('board') for s in e['sources'] if s['record'].get('board')), None)
            if not board_name:
                continue
            board = boards.get(board_name.casefold())
            if not board:
                stages = next((s['record'].get('stages') for s in e['sources'] if s['record'].get('stages')), None) or [graph.STALLED]
                board = boards[board_name.casefold()] = {'name': board_name, 'stages': [{'name': x} for x in stages if x != graph.STALLED] + [{'name': graph.STALLED, 'side': True}],
                                                         'projects': [], 'stuck': 0, 'team': True}
            names_ok = [x['name'] for x in board['stages']]
            stage = rec.get('stage') if rec.get('stage') in names_ok else names_ok[0]
            mover = names.get(next(((s['fleet'], s['member']) for s in e['sources'] if s['member'] == rec.get('stage_by')), None), '')
            since = graph.parse_time(rec.get('stage_at')) if rec.get('stage_at') else None
            days = max(0, (dt.datetime.now(dt.timezone.utc) - since).days) if since else 0
            best = max(e['sources'], key=lambda s: s['record'].get('score') or 0)['record']
            tile = {'id': e['local'] or e['key'], 'name': rec['name'], 'stage': stage, 'reason': rec.get('why') or best.get('why'),
                    'by': 'owner' if rec.get('stage_hand') else 'review', 'mover': _first_name(mover) if rec.get('stage_by') else None,
                    'at': rec.get('stage_at'), 'event_id': None, 'next': rec.get('next'), 'first': False, 'days': days, 'closed': False,
                    'no_reply_days': None, 'org': {'name': (rec.get('org') or {}).get('name'), 'logo': None} if rec.get('org') else None, 'card': None,
                    'owner': rec.get('owner'), 'owner_name': rec.get('owner_name'), 'fleet': e['sources'][0]['fleet'], 'via': via(e['sources']),
                    'team_only': not e['local']}
            if mine_tile:
                mine_tile.update(owner=tile['owner'], owner_name=tile['owner_name'], via=tile['via'], fleet=tile['fleet'], stage=stage if rec.get('stage_by') else mine_tile['stage'])
            else:
                board['projects'].append(tile)
        return [b for b in boards.values() if b['projects']]

    def team_section(self, db, entry):
        """What teammates shared about one entity: sources, timeline lines and client threads, each with whose it is."""
        lines, threads = [], []
        for s in entry['sources']:
            who = _first_name(s['name']) or 'a teammate'
            for x in s['record'].get('timeline') or []:
                lines.append({**x, 'via': who})
            for tag in s['record'].get('threads') or []:
                row = db.execute('SELECT body FROM team_mail WHERE fleet=? AND member=? AND tag=?', (s['fleet'], s['member'], tag)).fetchone()
                if row:
                    body = json.loads(row['body'])
                    if not body.get('private'):
                        threads.append({'id': f"{s['fleet']}:{s['member']}:{tag}", 'subject': body.get('subject'), 'at': body.get('at'),
                                        'messages': len(body.get('messages') or []), 'via': who})
        lines.sort(key=lambda x: str(x.get('at') or ''), reverse=True)
        threads.sort(key=lambda x: str(x.get('at') or ''), reverse=True)
        people = [{'member': s['member'], 'fleet': s['fleet'], 'name': _first_name(s['name']), 'left': s['left'],
                   'score': s['record'].get('score') or 0} for s in sorted(entry['sources'], key=lambda s: -(s['record'].get('score') or 0))]
        return {'via': via(entry['sources']), 'people': people, 'timeline': lines[:24], 'threads': threads[:20]}

    def team_for(self, db, node_id):
        ensure(db)
        entry = next((e for e in self.team_graph(db) if node_id in (e['local'], e['key'])), None)
        if not entry:
            return None, None
        return entry, self.team_section(db, entry)

    def team_detail(self, db, key):
        """A record only teammates have, opened from the Team view."""
        entry, section = self.team_for(db, key)
        if not entry:
            raise ValueError('Not in the graph')
        rec = resolve(entry)
        org = rec.get('org') or {}
        return {'id': entry['key'], 'kind': entry['kind'], 'name': rec['name'] or '?', 'role': rec['role'], 'team_only': True, 'links': [], 'recent': [],
                'probable_ids': [], 'last_at': rec['last'], 'strength': round(rec['score'], 2), 'closeness': graph.closeness(rec['score']), 'signals': 0,
                'org_name': org.get('name'), 'state': 'active', 'stage': rec.get('stage'), 'board': rec.get('board'), 'next': rec.get('next'), 'team': section}

    def team_thread(self, db, thread_id):
        """A client thread a teammate shared, in full: subject, messages with their text and their attachments (each one
        here to open, or too large to share)."""
        ensure(db)
        fleet, member, tag = (str(thread_id).split(':') + ['', '', ''])[:3]
        row = db.execute('SELECT body FROM team_mail WHERE fleet=? AND member=? AND tag=?', (fleet, member, tag)).fetchone()
        if not row:
            raise ValueError('Not shared with you')
        body = json.loads(row['body'])
        if body.get('private'):
            raise ValueError('Only the people on it read this one')
        source = db.execute('SELECT name,left_at FROM team_sources WHERE fleet=? AND member=?', (fleet, member)).fetchone()
        here = {r['tag'] for r in db.execute('SELECT tag FROM team_files WHERE fleet=? AND member=?', (fleet, member))}
        for m in body.get('messages') or []:
            for a in m.get('attachments') or []:
                if a.get('file') and a['file'] not in here:
                    a['waiting'] = True  # listed, its file not here yet
        return {**body, 'via': _first_name(source['name']) if source else '', 'left': bool(source and source['left_at'])}

    def team_file(self, db, thread_id, tag):
        """(bytes, mime, name) of an attachment that came with a client thread a teammate shared; only one its thread lists."""
        thread = self.team_thread(db, thread_id)
        if not HEX.fullmatch(str(tag)) or not any(a.get('file') == tag for m in thread.get('messages') or [] for a in m.get('attachments') or []):
            raise ValueError('Not shared with you')
        fleet, member = str(thread_id).split(':')[:2]
        row = db.execute('SELECT name,mime,data FROM team_files WHERE fleet=? AND member=? AND tag=?', (fleet, member, tag)).fetchone()
        if not row:
            raise ValueError('Not here yet')
        return nc.unb64(row['data']), row['mime'] or 'application/octet-stream', row['name'] or 'attachment'

    def held_back(self, db, days=7):
        """This week's Work threads that didn't go to the team as a whole (sensitive, or Only you by hand), to look over."""
        ensure(db)
        scopes = self.scopes(db)
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
        found = {}
        for fleet, scope in scopes.items():
            for e in _events(db, sorted(scope.accounts), since):
                e = dict(e)
                e['metadata'] = json.loads(e['metadata'] or '{}')
                view = visibility(db, scope, e)
                if view not in ('line', 'private'):
                    continue
                hand = db.execute('SELECT hand FROM share_threads WHERE thread=?', (thread_of(e['kind'], e['metadata'], e['id']),)).fetchone()
                if view == 'private' and not (hand and hand['hand'] == 'private'):
                    continue
                found[thread_of(e['kind'], e['metadata'], e['id'])] = {'event_id': e['id'], 'title': str(e['title'] or '')[:200], 'at': e['occurred_at'],
                                                                        'with': _who(e['metadata'], scope), 'share': 'only_you',
                                                                        'why': 'By hand' if view == 'private' else 'Private: the team sees only that it exists'}
        return sorted(found.values(), key=lambda x: x['at'], reverse=True)[:100]

    def graph_ask(self, fleet_ref, member_id, about, note):
        """Ask <name>: a handoff to the teammate closest to someone, about them. Inside the Fleet only."""
        import fleet_requests
        group = next((g for g in (self.directory().get('fleets') or []) if g.get('id') == fleet_ref and g.get('kind') == 'work'), None)
        if not group or not self.member(fleet_ref, member_id):
            raise ValueError('Not someone in that Fleet')
        question = fleet_requests.parse('handoff', {'title': 'About ' + str(about or '')[:80], 'summary': str(note or '').strip()[:1000], 'contact': ''})
        if not question:
            raise ValueError('Say what you need from them')
        return self.ask('handoff', question, context='graph:' + secrets.token_hex(8), fleet=fleet_ref, to=[member_id])

def via(sources):
    """'via Sam', 'via Sam and Kim', 'via Sam (left)'."""
    names = []
    for s in sorted(sources, key=lambda s: -(s['record'].get('score') or 0)):
        n = _first_name(s['name']) or 'a teammate'
        n += ' (left)' if s['left'] else ''
        if n not in names:
            names.append(n)
    if not names:
        return ''
    return 'via ' + (names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1])


def resolve(entry, local=None):
    """One entity's fields from every source (this Machine's own node first, as a source with its own score): by hand
    wins, else the closest."""
    cands = []
    if local:
        cands.append({'record': local, 'score': local.get('score') or 0, 'who': None})
    cands += [{'record': s['record'], 'score': s['record'].get('score') or 0, 'who': s} for s in entry['sources']]
    out = {}
    for field in ('name', 'role', 'org', 'next', 'board', 'why'):
        best = pick([{'value': c['record'].get(field), 'hand': c['record'].get(field + '_hand'), 'score': c['score']} for c in cands])
        out[field] = best['value'] if best else None
    stage = pick_stage([{'value': c['record'].get('stage'), 'hand': c['record'].get('stage_hand'), 'at': c['record'].get('stage_at'),
                         'score': c['score'], 'who': c['who'], 'board': c['record'].get('board')} for c in cands if c['record'].get('board')])
    if stage:
        out.update(stage=stage['value'], stage_hand=bool(stage['hand']), stage_at=stage['at'], stage_board=stage['board'],
                   stage_by=(stage['who'] or {}).get('member'))
    owner = max(cands, key=lambda c: c['score']) if cands else None
    out['owner'] = (owner['who'] or {}).get('member') if owner else None
    out['owner_name'] = _first_name((owner['who'] or {}).get('name')) if owner and owner['who'] else None
    out['score'] = max((c['score'] for c in cands), default=0)
    out['last'] = max((str(c['record'].get('last') or '') for c in cands), default='') or None
    return out


def _clean_text(value, chars):
    return ' '.join(str(value).split())[:chars] if isinstance(value, (str, int, float)) and str(value).strip() else None


def _clean_record(r):
    """A teammate's record in a fixed form: short text, hashes and numbers only."""
    text = lambda k, n: _clean_text(r.get(k), n)
    hashes = lambda v: [h for h in (v if isinstance(v, list) else [v]) if isinstance(h, str) and HEX.fullmatch(h)][:40]
    org = r.get('org') if isinstance(r.get('org'), dict) else None
    out = {'r': r['r'], 't': r['t'], 'name': text('name', 100) or '?', 'name_hand': r.get('name_hand') is True, 'role': text('role', 80),
           'role_hand': r.get('role_hand') is True, 'score': float(r['score']) if isinstance(r.get('score'), (int, float)) else 0.0,
           'last': text('last', 10), 'kv': text('kv', 16),
           'timeline': [{'at': _clean_text(x.get('at'), 25), 'line': _clean_text(x.get('line'), 160), **({'private': True} if x.get('private') else {}),
                         **({'tag': x['tag']} if HEX.fullmatch(str(x.get('tag'))) else {})}
                        for x in (r.get('timeline') or [])[:TIMELINE] if isinstance(x, dict) and _clean_text(x.get('line'), 160)]}
    if org:
        out['org'] = {'name': _clean_text(org.get('name'), 100), **({'d': hashes(org['d'] if isinstance(org.get('d'), list) else [org.get('d')])} if org.get('d') else {})}
        out['org_hand'] = r.get('org_hand') is True
    out['threads'] = hashes(r.get('threads'))[:40] if r['t'] != 'org' else []
    if r['t'] == 'person':
        out.update(ids=hashes(r.get('ids')), d=hashes(r.get('d')))
    elif r['t'] == 'org':
        out['d'] = hashes(r.get('d'))
    else:
        stages = [s for s in (r.get('stages') or []) if _clean_text(s, 40)][:8] if isinstance(r.get('stages'), list) else None
        out.update(board=text('board', 60), stages=[_clean_text(s, 40) for s in stages] if stages else None, stage=text('stage', 40),
                   stage_hand=r.get('stage_hand') is True, stage_at=text('stage_at', 25), why=text('why', 160), next=text('next', 120),
                   people=hashes(r.get('people')))
    return out


def _mime(value):
    value = str(value or '').strip().lower()
    return value if re.fullmatch(r'[a-z0-9.+-]{1,40}/[a-z0-9.+-]{1,60}', value) else 'application/octet-stream'


def _clean_mail(body, tag):
    if body.get('private'):
        return {'tag': tag, 'private': True, 'at': _clean_text(body.get('at'), 25), 'line': _clean_text(body.get('line'), 120) or 'Private thread'}
    messages = []
    for m in (body.get('messages') or [])[:MAX_MESSAGES]:
        if isinstance(m, dict):
            messages.append({'at': _clean_text(m.get('at'), 25), 'from': _clean_text(m.get('from'), 200), 'to': _clean_text(m.get('to'), 500),
                             'cc': _clean_text(m.get('cc'), 500), 'body': str(m.get('body') or '')[:MAX_BODY],
                             'attachments': [{'name': _clean_text(a.get('name'), 120) or 'attachment', 'size': a.get('size') if isinstance(a.get('size'), int) else None,
                                              **({'file': a['file']} if HEX.fullmatch(str(a.get('file'))) else {'too_large': True} if a.get('too_large') else {'not_shared': True} if a.get('not_shared') else {})}
                                             for a in (m.get('attachments') or [])[:20] if isinstance(a, dict)]})
    return {'tag': tag, 'subject': _clean_text(body.get('subject'), 200), 'at': _clean_text(body.get('at'), 25), 'messages': messages}
