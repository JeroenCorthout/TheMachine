"""People and projects: a living graph over the collected signals.

Rebuilt from scratch every few minutes as a pure function of the signals, card decisions, project
labels and merges, so nothing drifts and a rule change applies to the past too. Every interaction
adds weight that fades by half per half-life (closeness 60 days, people seen together 45, a
person's part in a project 45, a project's heat 21); a day counts at most once per person, so a
burst of chat messages is one day of contact. The owner's own messages count double theirs, a
meeting more, a newsletter or mass mailing not at all. Card decisions teach it too: Do it, Send
and Mark done strengthen the people on the evidence, Not relevant weakens them.

People are matched by identifiers only (email address, phone, Slack user, LinkedIn or X
conversation, GitHub login), joined when Slack reports the user's email, when the same full name
appears on two different channels, or when the owner or agent says so (merges). Projects are named
by the review agent and the owner, and spread to the rest of their threads; a signal that reads like
a labelled one and shares a person with it is probably part of it too (weaker, until a review
confirms). Threads that read alike and share people, but nobody named, are offered to a review as
possibly one piece of work. People and organisations get a role (what they are to the owner), said
by the owner or by a review that read it in a message (kept with the line it came from, so the owner
can confirm it); the sources only give hints. A person's organisation comes from their work email domain, or
from a source that states it plainly (their LinkedIn headline, an email signature, them saying where they work),
read by a review and kept with that line, where and when it was said; nothing is guessed. People can be related to each other ("assistant of",
"introduced by"). Going quiet compares the silence with that person's own usual gap. Messages are
never deleted; links that faded away are just not stored."""
from __future__ import annotations

import collections
import datetime as dt
import email.utils
import json
import math
import operator
import os
import re
import sqlite3
import struct
import unicodedata
from zoneinfo import ZoneInfo

import enrichment

SCHEMA = '''
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE COLLATE NOCASE,created_at TEXT NOT NULL,closed_at TEXT);
CREATE TABLE IF NOT EXISTS event_projects(event_id INTEGER NOT NULL,project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(event_id,project_id));
CREATE TABLE IF NOT EXISTS identity_names(identifier TEXT PRIMARY KEY,name TEXT,email TEXT,checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_merges(a TEXT NOT NULL,b TEXT NOT NULL,same INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS graph_nodes(id TEXT PRIMARY KEY,kind TEXT NOT NULL,name TEXT NOT NULL,strength REAL NOT NULL,
  last_at TEXT,signals INTEGER NOT NULL,gap_days REAL,quiet INTEGER NOT NULL DEFAULT 0,trend REAL,info TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_links(a TEXT NOT NULL,b TEXT NOT NULL,kind TEXT NOT NULL,strength REAL NOT NULL,last_at TEXT,PRIMARY KEY(a,b));
CREATE INDEX IF NOT EXISTS graph_links_b ON graph_links(b);
CREATE TABLE IF NOT EXISTS graph_events(node_id TEXT NOT NULL,event_id INTEGER NOT NULL,at TEXT NOT NULL,probable INTEGER NOT NULL DEFAULT 0,direct INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(node_id,event_id));
CREATE INDEX IF NOT EXISTS graph_events_event ON graph_events(event_id);
CREATE TABLE IF NOT EXISTS graph_state(id INTEGER PRIMARY KEY CHECK(id=1),built_at TEXT NOT NULL,signals INTEGER NOT NULL,took_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS person_roles(identifier TEXT PRIMARY KEY,role TEXT NOT NULL,source TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_unnamed(key INTEGER PRIMARY KEY,event_ids TEXT NOT NULL,people TEXT NOT NULL,titles TEXT NOT NULL,size INTEGER NOT NULL,last_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_offers(key INTEGER PRIMARY KEY,size INTEGER NOT NULL,run_id TEXT,offered_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_names(node_id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_role_asks(identifier TEXT PRIMARY KEY,run_id TEXT,asked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS role_corrections(id INTEGER PRIMARY KEY,identifier TEXT NOT NULL,name TEXT NOT NULL,old_role TEXT NOT NULL,
  new_role TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_profiles(domain TEXT PRIMARY KEY,status TEXT NOT NULL,profile TEXT,fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_relations(a TEXT NOT NULL,b TEXT NOT NULL,relation TEXT NOT NULL,source TEXT NOT NULL,event_id INTEGER,
  created_at TEXT NOT NULL,PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS org_edits(domain TEXT PRIMARY KEY,fields TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hidden_domains(domain TEXT PRIMARY KEY,hidden INTEGER NOT NULL,source TEXT NOT NULL,event_id INTEGER,
  name TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS person_orgs(identifier TEXT PRIMARY KEY,org TEXT NOT NULL,org_id TEXT,source TEXT NOT NULL,via TEXT,event_id INTEGER,
  quote TEXT,said_at TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_org_asks(identifier TEXT PRIMARY KEY,run_id TEXT,asked_at TEXT NOT NULL);
'''
RECHECK_DAYS = 90       # a review's role is read again after this, when there was contact since
HALF_LIFE = {'person': 60, 'pair': 45, 'project_person': 45, 'project': 21}
DAY_CAP = 1.0           # one day of contact, however many messages
FLOOR = 0.01            # faded below this: not stored
HORIZON_DAYS = 400      # older signals have faded far below the floor anyway
EVENTS_PER_NODE = 40
BULK_LABELS = {'CATEGORY_PROMOTIONS', 'CATEGORY_SOCIAL', 'CATEGORY_FORUMS', 'CATEGORY_UPDATES'}
BULK = re.compile(r'(?:^|[._+-])(?:no-?reply|do-?not-?reply|notifications?|notify|newsletters?|news|mailer-daemon|mailer|bounces?|postmaster|'
                  r'marketing|digest|alerts?|automated|receipts?|invoices?|billing)(?:[._+-]|@)|@(?:[\w-]+\.)*(?:mcsv\.net|mcdlv\.net|'
                  r'sendgrid\.net|amazonses\.com|mailgun\.org|bounces?\.[\w.-]+|calendar\.google\.com|resource\.calendar\.google\.com)$', re.I)
FREEMAIL = {'gmail.com', 'googlemail.com', 'hotmail.com', 'hotmail.be', 'outlook.com', 'outlook.be', 'live.com', 'live.be', 'msn.com',
            'yahoo.com', 'yahoo.fr', 'icloud.com', 'me.com', 'mac.com', 'proton.me', 'protonmail.com', 'telenet.be', 'skynet.be',
            'gmx.com', 'gmx.de', 'web.de', 'aol.com', 'pandora.be', 'scarlet.be', 'proximus.be', 'yandex.com', 'zoho.com'}
ROLE = re.compile(r'^(?:info|support|hello|hi|team|sales|billing|admin|office|contact|help|accounts?|finance|jobs|hr|press|legal|security|privacy|feedback)@')
GENERIC_NAMES = {'you', 'linkedin member', 'x conversation', 'linkedin conversation', 'unknown'}
WEIGHTS = {'owner': 0.6, 'them': 0.3, 'met': 0.5}
DECISION_WEIGHT = {'accept': 1.0, 'send': 1.0, 'complete': 1.0, 'other': 0.5, 'reject': -0.5}
# Cosine between local embeddings (see embeddings.py). Meaning alone groups by genre (every partnership
# email reads alike), so a probable link also needs a shared person; tried on live data, 2026-10-18.
PROBABLE = 0.72
UNNAMED = 0.75
UNNAMED_DAYS = 45
UNNAMED_KINDS = {'gmail.message', 'calendar.event', 'salesflare.email', 'salesflare.meeting', 'intercom.conversation', 'github.notification'}
OPEN_CARD = ('pending', 'accepted', 'prepared', 'instructions', 'snoozed')


def ensure(db):
    """Create the graph tables, and add columns that came later."""
    db.executescript(SCHEMA)
    if 'probable' not in {r[1] for r in db.execute('PRAGMA table_info(graph_events)')}:
        db.execute('ALTER TABLE graph_events ADD COLUMN probable INTEGER NOT NULL DEFAULT 0')
    if 'direct' not in {r[1] for r in db.execute('PRAGMA table_info(graph_events)')}:
        db.execute('ALTER TABLE graph_events ADD COLUMN direct INTEGER NOT NULL DEFAULT 0')
    have = {r[1] for r in db.execute('PRAGMA table_info(person_roles)')}
    for column in ('event_id INTEGER', 'quote TEXT', 'hints TEXT', 'checked_at TEXT'):  # a role's evidence and when it was last read
        if column.split()[0] not in have:
            db.execute('ALTER TABLE person_roles ADD COLUMN ' + column)
    if 'people_shown' not in {r[1] for r in db.execute('PRAGMA table_info(graph_role_asks)')}:  # the ask showed an organisation's people with their roles
        db.execute('ALTER TABLE graph_role_asks ADD COLUMN people_shown INTEGER NOT NULL DEFAULT 0')


class Owner:
    """The owner's own identities, from the configured accounts: never a person in the graph."""
    def __init__(self, config):
        accounts = config.get('accounts', [])
        self.emails = {str(a.get('email', '')).lower() for a in accounts if '@' in str(a.get('email', ''))}
        for a in accounts:
            self.emails |= {str(e).lower() for e in a.get('owner_emails') or () if '@' in str(e)}
        self.emails |= {str(e).lower() for e in config.get('owner_emails') or ()}
        self.domains = {e.rsplit('@', 1)[1] for e in self.emails} - FREEMAIL
        self.slack = {a['user_id'] for a in accounts if a.get('service') == 'slack' and a.get('user_id')}
        self.handles = {str(a.get('handle') or a.get('login') or '').lower() for a in accounts if a.get('service') in ('x', 'github')} - {''}
        self.names = set()

    def learn(self, signals):
        """Addresses the owner sends from (aliases, an older personal address) and the name on them."""
        for _, _, kind, _, m in signals:
            if kind == 'gmail.message' and 'SENT' in (m.get('labels') or ()):
                for name, addr in addresses(m.get('from'))[:1]:
                    self.emails.add(addr)
                    if plain_name(name):
                        self.names.add(plain_name(name))

    def mine(self, address):
        """The owner's own address, or a shared role address (info@, support@) at the owner's domains."""
        return address in self.emails or (address.rsplit('@', 1)[-1] in self.domains and bool(ROLE.match(address)))


def addresses(value):
    if isinstance(value, list):
        value = ', '.join(map(str, value))
    return [(name.strip().strip('"'), addr.strip().lower()) for name, addr in email.utils.getaddresses([str(value or '')]) if '@' in addr]


def bulk(address):
    return bool(BULK.search(address))


SUFFIX = re.compile(r'\s+(?:[|/\-–—•·:@]|via|from|at|for)\s+.*$')
DEGREE = re.compile(r'^(?:[A-Z]{2,6}|Ph\.?D\.?|MSc|BSc|Jr\.?|Sr\.?)$')


def person_name(name):
    """Only the name, without what people or their tools add to it: "Kim Lee (Initech)", "Ann Bell | Acme",
    "Sam at Globex", "(second) Pat Lee", "Doe EXT, Sam" and "Smith, PhD". "Doe, Jane" is
    turned around. Left as it was when nothing that looks like a name is left."""
    raw = ' '.join(str(name or '').replace('"', ' ').split())
    text = re.sub(r'[\(\[\{<][^)\]}>]*[\)\]}>]', ' ', raw)  # (Initech), [Acme], (@handle), <note>
    text = SUFFIX.sub('', ' '.join(text.split()))  # "| Acme", "- Acme", "at Acme", "via LinkedIn"
    text = ''.join(c for c in text if not unicodedata.category(c).startswith('S'))  # emoji and symbols
    text = re.sub(r'\b(?:EXT|Ext|ext|EXTERNAL|External)\b\.?', ' ', text)
    parts = [p.strip() for p in text.split(',')]
    while len(parts) > 1 and (DEGREE.match(parts[-1]) or not parts[-1]):
        parts.pop()  # ", PhD", ", DBA, MPA"
    text = ', '.join(parts)
    if len(parts) == 2:
        before, after = parts
        if before and 1 <= len(after.split()) <= 3 and len(before.split()) <= 3 and not re.search(r'\d', after):
            text = f'{after} {before}'  # "Last, First"
    text = ' '.join(text.strip(' ,;:-–—|/').split())
    return text if re.search(r'[^\W\d_]{2}', text) else raw


def plain_name(name):
    """Comparable full name: accents, case, punctuation and extra spaces removed; None unless it
    looks like a person's full name (two or more words, no address)."""
    name = person_name(name)
    if not name or '@' in name or name.casefold() in GENERIC_NAMES:
        return None
    folded = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().casefold()
    words = re.findall(r'[a-z]+', folded)
    return ' '.join(words) if len(words) >= 2 and all(len(w) > 1 for w in words[:2]) else None


def touches(kind, m, owner, wrote_to):
    """[(identifier, name, weight, role)] for one signal. Roles: 'owner' (the owner wrote to them),
    'them' (they wrote to the owner directly), 'met' (a meeting with the owner), 'seen' (present)."""
    if kind == 'gmail.message':
        labels = set(m.get('labels') or ())
        if labels & {'SPAM', 'TRASH'}:
            return []
        sender = addresses(m.get('from'))[:1]
        to, cc = addresses(m.get('to')), addresses(m.get('cc'))
        if 'SENT' in labels or (sender and sender[0][1] in owner.emails):
            to = [(n, a) for n, a in to if not owner.mine(a)]
            cc = [(n, a) for n, a in cc if not owner.mine(a)]
            count = len(to) + len(cc)
            if not count or count > 20:
                return []  # a note to self, or a mass mailing
            share = 1 / math.sqrt(count)
            return [('email:' + a, n, WEIGHTS['owner'] * share, 'owner') for n, a in to] + [('email:' + a, n, 0.3 * share, 'owner') for n, a in cc]
        if not sender or owner.mine(sender[0][1]):
            return []
        name, addr = sender[0]
        if (labels & BULK_LABELS or bulk(addr)) and addr not in wrote_to:
            return []
        direct = any(owner.mine(a) for _, a in to)
        # Unsolicited mail counts half until the owner has written to them.
        out = [('email:' + addr, name, (WEIGHTS['them'] if addr in wrote_to else 0.15) if direct else 0.1, 'them' if direct else 'seen')]
        others = [(n, a) for n, a in to + cc if not owner.mine(a) and a != addr and not bulk(a)]
        return out + ([('email:' + a, n, 0.05, 'seen') for n, a in others] if len(others) <= 8 else [])
    if kind == 'calendar.event':
        if m.get('status') == 'cancelled':
            return []
        attendees = [a for a in m.get('attendees') or () if isinstance(a, dict)]
        organizer = m.get('organizer') if isinstance(m.get('organizer'), dict) else {}
        if len(attendees) > 25:
            return []
        mine = any(a.get('self') or str(a.get('email', '')).lower() in owner.emails for a in attendees + [organizer])
        people = {}
        for a in attendees + [organizer]:
            addr = str(a.get('email') or '').lower()
            if ('@' in addr and not a.get('self') and not a.get('resource') and not owner.mine(addr)
                    and a.get('responseStatus') != 'declined' and not bulk(addr)):
                people.setdefault(addr, a.get('displayName'))
        if not people:
            return []
        share = (WEIGHTS['met'] if mine else 0.1) / math.sqrt(len(people))
        return [('email:' + a, n, share, 'met' if mine else 'seen') for a, n in people.items()]
    if kind == 'whatsapp.message':
        chat = str(m.get('chat_id') or '')
        mine = m.get('from_me') is True
        if chat.endswith('@g.us'):
            sender = str(m.get('sender_id') or '')
            return [] if mine or '@' not in sender else [('wa:' + sender.split('@')[0], m.get('from'), 0.1, 'seen')]
        if '@' not in chat or chat.endswith('@broadcast') or chat.startswith('status@'):
            return []
        return [('wa:' + chat.split('@')[0], m.get('chat_name'), WEIGHTS['owner' if mine else 'them'], 'owner' if mine else 'them')]
    if kind == 'slack.message':
        if m.get('bot_id') or m.get('subtype') in ('bot_message', 'channel_join', 'channel_leave'):
            return []
        user, kind_ = m.get('user_id'), m.get('conversation_type')
        if kind_ == 'dm':
            partner = re.fullmatch(r'DM ([UW][A-Z0-9]+)', str(m.get('channel_name') or ''))
            if not partner or partner[1] in owner.slack:
                return []
            mine = user in owner.slack
            return [('slack:' + partner[1], None, WEIGHTS['owner' if mine else 'them'], 'owner' if mine else 'them')]
        if not user or user in owner.slack:
            return []
        return [('slack:' + user, None, 0.15 if kind_ == 'group' else 0.05, 'seen')]
    if kind == 'linkedin.message':
        if m.get('group') or not m.get('conversation_id'):
            return []
        mine = m.get('from_me') is True
        return [('li:' + str(m['conversation_id']), m.get('chat_name'), WEIGHTS['owner' if mine else 'them'], 'owner' if mine else 'them')]
    if kind == 'x.dm':
        if m.get('group'):
            return []
        mine = m.get('from_me') is True
        sender = str(m.get('sender_id') or '')
        other = [p for p in str(m.get('conversation_id') or '').split('-') if p and p != sender] if mine else [sender]
        if len(other) != 1 or not other[0]:
            return []
        return [('x:' + other[0], m.get('chat_name'), WEIGHTS['owner' if mine else 'them'], 'owner' if mine else 'them')]
    if kind == 'x.mention':
        if not m.get('author_id') or str(m.get('screen_name') or '').lower() in owner.handles:
            return []
        return [('x:' + str(m['author_id']), m.get('from'), 0.2 if m.get('reply_to_account') else 0.1, 'seen')]
    if kind == 'github.notification':
        login = str(m.get('from') or '').lower()
        return [] if not login or m.get('from_me') or login in owner.handles or login.endswith('[bot]') else [('gh:' + login, m.get('from'), 0.1, 'seen')]
    if kind == 'intercom.conversation':
        found = addresses(m.get('from'))
        return [('email:' + found[0][1], None, 0.05, 'seen')] if found and not owner.mine(found[0][1]) and not bulk(found[0][1]) else []
    if kind == 'stripe.event':
        found = addresses(m.get('from'))
        return [('email:' + found[0][1], None, 0.05, 'seen')] if found and not owner.mine(found[0][1]) and not bulk(found[0][1]) else []
    if kind == 'owner.card':
        # A card the owner made about someone (New card, Who): that person, lightly, as one the card is about.
        return [(str(p['id']), p.get('name'), 0.05, 'about') for p in (m.get('about') or ())[:5]
                if isinstance(p, dict) and p.get('kind') == 'person' and str(p.get('id') or '').strip()]
    if kind in ('salesflare.email', 'salesflare.meeting'):
        contacts = [c for c in m.get('contacts') or () if isinstance(c, dict) and '@' in str(c.get('email') or '')][:5]
        met = kind == 'salesflare.meeting' and str(m.get('host') or '').lower() in owner.emails
        return [('email:' + str(c['email']).lower(), c.get('name'), (WEIGHTS['met'] / math.sqrt(len(contacts))) if met else 0.05,
                 'met' if met else 'seen') for c in contacts if not owner.mine(str(c['email']).lower()) and not bulk(str(c['email']).lower())]
    return []


def thread_key(kind, m):
    """(key, exact): signals sharing an exact key are one thread; a chat key holds only within days."""
    if kind == 'gmail.message' and m.get('thread_id'):
        return 'gm:' + str(m['thread_id']), True
    if kind == 'slack.message' and m.get('channel_id'):
        stamp = m.get('thread_ts') or m.get('message_ts')
        return ('sl:' + str(m['channel_id']) + ':' + str(stamp), True) if stamp else (None, False)
    if kind in ('whatsapp.message',) and m.get('chat_id'):
        return 'wa:' + str(m['chat_id']), False
    if kind in ('linkedin.message', 'x.dm') and m.get('conversation_id'):
        return kind + ':' + str(m['conversation_id']), False
    if kind == 'x.mention' and m.get('conversation_id'):
        return 'xc:' + str(m['conversation_id']), True
    if kind == 'salesflare.email' and m.get('thread_id'):
        return 'sf:' + str(m['thread_id']), True
    if kind == 'intercom.conversation' and m.get('conversation_id'):
        return 'ic:' + str(m['conversation_id']), True
    if kind == 'github.notification' and m.get('repo') and m.get('number'):
        return 'gh:' + str(m['repo']) + '#' + str(m['number']), True
    if kind == 'calendar.event' and m.get('recurring_event_id'):
        return 'cal:' + str(m['recurring_event_id']), True
    return None, False


class Union:
    """Identifiers joined into people; a pair said to be different is never joined, directly or through others."""
    def __init__(self, apart=()):
        self.parent, self.members, self.apart = {}, {}, set(apart)

    def find(self, x):
        if x not in self.parent:
            self.parent[x], self.members[x] = x, {x}
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b or any(frozenset((x, y)) in self.apart for x in self.members[a] for y in self.members[b]):
            return
        # An email address names the node when there is one: stable and recognisable.
        if b.startswith('email:') and not a.startswith('email:'):
            a, b = b, a
        self.parent[b] = a
        self.members[a] |= self.members.pop(b)


def parse_time(value):
    at = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return at if at.tzinfo else at.replace(tzinfo=dt.timezone.utc)


def fade(weight, age_days, half_life):
    return weight * 0.5 ** (max(0.0, age_days) / half_life)


def shown_id(identifier, x_names):
    space, _, value = identifier.partition(':')
    return {'email': value, 'wa': '+' + value if value.isdigit() else 'WhatsApp', 'slack': 'Slack', 'li': 'LinkedIn',
            'x': '@' + x_names[identifier] if x_names.get(identifier) else 'X', 'gh': 'GitHub ' + value}.get(space, identifier)


def load_vectors(db, event_ids, settings=None):
    """Unit vectors by event, when embeddings are on; empty otherwise."""
    try:
        rows = db.execute('SELECT v.event_id,v.vector,v.model,v.dims FROM event_vectors v JOIN events e ON e.id=v.event_id WHERE e.deleted=0 AND e.version=v.version').fetchall()
    except sqlite3.OperationalError:
        return {}
    # Without explicit settings (offline callers), use one namespace, never mixed models.
    namespace = (settings.get('model', ''), int(settings.get('dims', 256))) if settings is not None else next(((r[2], r[3]) for r in rows if r[0] in event_ids), None)
    out = {}
    for eid, blob, model, dims in rows:
        if eid not in event_ids or (model, dims) != namespace or dims < 1 or len(blob) != dims * 4:
            continue
        vector = struct.unpack('<%df' % dims, blob)
        if all(math.isfinite(v) for v in vector):
            out[eid] = vector
    return out


def cosine(a, b):
    return sum(map(operator.mul, a, b))


def probable_projects(labelled, event_people, vectors, times, current, open_projects, kinds=None):
    """Signals that read like a labelled one (cosine >= PROBABLE) and share a person with it:
    {event_id: {project ids}}. Only for open projects and signals no label reached, and not for
    meetings: a bare calendar title reads like every other one with the same people."""
    by_person = collections.defaultdict(list)
    for event_id, ids in labelled.items():
        ids = ids & open_projects
        if ids and event_id in vectors:
            for node in event_people.get(event_id, ()):
                by_person[node].append((event_id, ids))
    found = {}
    for event_id, people in event_people.items():
        if event_id in labelled or event_id not in vectors or times.get(event_id, current) > current or (kinds or {}).get(event_id) == 'calendar.event':
            continue
        vector, best = vectors[event_id], set()
        for node in people:
            for other, ids in by_person.get(node, ()):
                if not ids <= best and cosine(vector, vectors[other]) >= PROBABLE:
                    best |= ids
        if best:
            found[event_id] = best
    return found


def unnamed_work(per_event, event_people, owner_events, labelled, vectors, current):
    """Threads from the last weeks that read alike and share a person, with no project: possibly one
    piece of work nobody named. [(event ids, people by weight, last time)]. Chats are left out (they
    read alike per person, whatever the topic), and so are groups of only meetings (a calendar reads
    alike too); the owner must have written or met in at least one."""
    since = current - dt.timedelta(days=UNNAMED_DAYS)
    threads = collections.defaultdict(list)
    for event_id, (kind, at, account_id, m, _) in per_event.items():
        if kind in UNNAMED_KINDS and since <= at <= current and event_id not in labelled and event_id in vectors:
            key, exact = thread_key(kind, m)
            threads[(account_id, key) if key and exact else ('event', event_id)].append(event_id)
    units = []
    for key, ids in threads.items():
        total = [sum(values) for values in zip(*(vectors[e] for e in ids))]
        size = math.sqrt(sum(v * v for v in total)) or 1.0
        people = {p for e in ids for p in event_people.get(e, ())}
        units.append({'ids': ids, 'vector': [v / size for v in total], 'people': people,
                      'meeting': all(per_event[e][0] == 'calendar.event' for e in ids), 'mine': any(e in owner_events for e in ids)})
    by_person = collections.defaultdict(list)
    for index, unit in enumerate(units):
        for node in unit['people']:
            by_person[node].append(index)
    union = Union()
    compared = set()
    for indexes in by_person.values():
        if len(indexes) > 150:
            continue  # someone on everything says nothing about which work
        for i, a in enumerate(indexes):
            for b in indexes[i + 1:]:
                if (a, b) not in compared:
                    compared.add((a, b))
                    if cosine(units[a]['vector'], units[b]['vector']) >= UNNAMED:
                        union.join(str(a), str(b))
    groups = collections.defaultdict(list)
    for index in range(len(units)):
        groups[union.find(str(index))].append(units[index])
    out = []
    for members in groups.values():
        if len(members) < 3 or sum(not u['meeting'] for u in members) < 2 or not any(u['mine'] for u in members):
            continue
        ids = sorted(e for u in members for e in u['ids'])
        weight = collections.Counter(p for e in ids for p in event_people.get(e, ()))
        out.append((ids, [p for p, _ in weight.most_common()], max(per_event[e][1] for e in ids)))
    return sorted(out, key=lambda g: g[2], reverse=True)


def owner_tz(config):
    try:
        return ZoneInfo(config.get('timezone') or os.environ.get('ASSISTANT_TIMEZONE') or 'Europe/Amsterdam')
    except (ValueError, KeyError):
        return dt.timezone.utc


def build(db, config, now):
    """Publish one coherent graph. Concurrent edits cannot be overwritten by an older build."""
    db.execute('BEGIN IMMEDIATE')
    try:
        return _build(db, config, now)
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise


def _build(db, config, now):
    """Recompute graph_nodes, graph_links and graph_events. Returns a short summary."""
    started = dt.datetime.now(dt.timezone.utc)
    current = parse_time(now)
    owner = Owner(config)
    tz = owner_tz(config)
    horizon = (current - dt.timedelta(days=HORIZON_DAYS)).isoformat()
    rows = db.execute("SELECT id,account_id,kind,occurred_at,metadata FROM events WHERE deleted=0 AND occurred_at>=? ORDER BY occurred_at", (horizon,)).fetchall()
    signals = []
    for r in rows:
        try:
            m = json.loads(r['metadata'])
        except ValueError:
            continue
        signals.append((r['id'], r['account_id'], r['kind'], parse_time(r['occurred_at']), m if isinstance(m, dict) else {}))
    owner.learn(signals)
    wrote_to = set()
    for _, _, kind, _, m in signals:
        if kind == 'gmail.message' and ('SENT' in (m.get('labels') or ()) or any(a in owner.emails for _, a in addresses(m.get('from'))[:1])):
            wrote_to |= {a for _, a in addresses(m.get('to')) + addresses(m.get('cc'))}

    warmup = hidden(db)  # addresses there are a warm-up tool's traffic: nobody in the graph

    # Who: identifiers per signal, joined into people.
    seen_message_ids = set()
    per_event, names, x_names = {}, collections.defaultdict(collections.Counter), {}
    crm_names, paying_names = {}, {}
    appears = collections.defaultdict(set)  # identifier -> where they show up: a hint for a review, never a role
    owner_events = set()
    headlines = set()  # LinkedIn conversations whose person has a headline (read at view time: it stays sealed in the signal)
    for event_id, account_id, kind, at, m in signals:
        if kind == 'gmail.message' and m.get('message-id'):
            if m['message-id'] in seen_message_ids:
                continue  # the same email in two mailboxes counts once
            seen_message_ids.add(m['message-id'])
        crm = m.get('crm_account') if isinstance(m.get('crm_account'), dict) else None
        if crm and crm.get('domain') and crm.get('name'):
            crm_names[str(crm['domain']).lower()] = str(crm['name'])
        if kind in ('salesflare.email', 'salesflare.meeting'):
            for c in m.get('contacts') or ():
                if isinstance(c, dict) and '@' in str(c.get('email') or ''):
                    appears['email:' + str(c['email']).lower()].add('a Salesflare contact' + (' at ' + str(crm['name']) if crm and crm.get('name') else ''))
        elif kind in ('stripe.event', 'intercom.conversation'):
            for _, addr in addresses(m.get('from'))[:1]:
                if addr.rsplit('@', 1)[1] in owner.domains:
                    continue  # a teammate answering support or sending an invoice
                appears['email:' + addr].add(('billed through Stripe' + (' as ' + str(m['customer_name']) if m.get('customer_name') else ''))
                                             if kind == 'stripe.event' else 'wrote to support (Intercom)')
                domain = addr.rsplit('@', 1)[1]
                if kind == 'stripe.event' and m.get('customer_name') and domain not in FREEMAIL:
                    paying_names.setdefault(domain, str(m['customer_name']))
        found = [t for t in touches(kind, m, owner, wrote_to) if not (t[0].startswith('email:') and t[0].rsplit('@', 1)[1] in warmup)]
        if not found:
            continue
        if kind == 'linkedin.message' and isinstance(m.get('headline'), str) and m['headline'].strip() and not m.get('group'):
            headlines.add('li:' + str(m['conversation_id']))
        per_event[event_id] = (kind, at, account_id, m, found)
        if any(role in ('owner', 'met') for _, _, _, role in found):
            owner_events.add(event_id)
        for identifier, name, _, _ in found:
            if name and str(name).strip() and '@' not in str(name) and str(name).strip().casefold() not in GENERIC_NAMES:
                names[identifier][person_name(name)] += 1
            if identifier.startswith('x:') and m.get('screen_name') and not m.get('from_me'):
                x_names[identifier] = m['screen_name']
    merges = db.execute('SELECT a,b,same FROM graph_merges ORDER BY created_at').fetchall()
    union = Union(frozenset((r['a'], r['b'])) for r in merges if not r['same'])
    known = {identifier for (_, _, _, _, found) in per_event.values() for identifier, _, _, _ in found}
    for identifier in sorted(known):
        union.find(identifier)
    for r in merges:
        if r['same']:
            union.join(r['a'], r['b'])
    for r in db.execute('SELECT identifier,name,email FROM identity_names'):
        if r['name']:
            names[r['identifier']][person_name(r['name'])] += 3
        if r['email'] and r['identifier'] in known:
            union.join('email:' + r['email'].lower(), r['identifier'])
    by_name = collections.defaultdict(set)
    for identifier in known:
        best = names[identifier].most_common(1)
        full = plain_name(best[0][0]) if best else None
        if full:
            by_name[full].add(identifier)
    for full, group in by_name.items():
        if full in owner.names:
            continue  # the owner, writing from another channel
        spaces = collections.Counter(i.split(':', 1)[0] for i in group)
        # The same full name on different channels is one person, unless it's ambiguous
        # within a channel (two addresses, two chats) or someone said they differ.
        if len(spaces) > 1 and all(c == 1 for c in spaces.values()):
            group = sorted(group, key=lambda i: (not i.startswith('email:'), i))
            for other in group[1:]:
                union.join(group[0], other)

    # How strong: per day, capped; faded by age.
    day_weight = collections.defaultdict(float)
    roles = collections.defaultdict(lambda: collections.defaultdict(set))
    pair_day = collections.defaultdict(float)
    node_events = collections.defaultdict(list)
    event_people, direct_people = {}, {}
    next_meeting, accounts = {}, collections.defaultdict(set)
    for event_id, (kind, at, account_id, m, found) in per_event.items():
        people = {}
        for identifier, _, weight, role in found:
            node = union.find(identifier)
            people[node] = max(people.get(node, 0), weight)
            if at > current:
                if role == 'met' and (node not in next_meeting or at < next_meeting[node]):
                    next_meeting[node] = at
                continue
            day = at.astimezone(tz).date()  # the owner's day: a message at 00:17 on Sunday is Sunday
            day_weight[(node, day)] += weight
            roles[node][role].add(day)
            accounts[node].add(account_id)
        event_people[event_id] = list(people)
        direct_people[event_id] = [union.find(i) for i, _, weight, role in found if role != 'seen']
        for node in people:
            node_events[node].append((at, event_id))
        if at <= current and 2 <= len(people) <= 8:
            ordered = sorted(people)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1:]:
                    pair_day[(a, b, at.date())] = min(0.5, pair_day[(a, b, at.date())] + 0.2 / (len(people) - 1))

    def age(day):
        return (current - dt.datetime.combine(day, dt.time(12), dt.timezone.utc)).total_seconds() / 86400

    strength, last_day = collections.defaultdict(float), {}
    for (node, day), weight in day_weight.items():
        strength[node] += fade(min(DAY_CAP, weight), age(day), HALF_LIFE['person'])
        last_day[node] = max(last_day.get(node, day), day)

    # Projects: labels, spread to the rest of their threads.
    projects = {r['id']: dict(r) for r in db.execute('SELECT id,name,created_at,closed_at FROM projects')}
    labelled = collections.defaultdict(set)
    for r in db.execute('SELECT event_id,project_id FROM event_projects'):
        if r['project_id'] in projects:
            labelled[r['event_id']].add(r['project_id'])
    threads = collections.defaultdict(list)
    for event_id, account_id, kind, at, m in signals:
        key, exact = thread_key(kind, m)
        if key:
            threads[(account_id, key)].append((at, event_id, exact))
    spread = {e: set(p) for e, p in labelled.items()}
    for members in threads.values():
        tagged = [(at, labelled[e]) for at, e, _ in members if e in labelled]
        if not tagged:
            continue
        for at, event_id, exact in members:
            for tagged_at, ids in tagged:
                if exact or abs((at - tagged_at).total_seconds()) <= 2 * 86400:
                    spread.setdefault(event_id, set()).update(ids)
    times = {event_id: at for event_id, _, _, at, _ in signals}
    vectors = load_vectors(db, set(per_event), config['embeddings']) if isinstance(config.get('embeddings'), dict) and config['embeddings'].get('enabled') is not False else {}
    probable = probable_projects(spread, event_people, vectors, times, current, {pid for pid, p in projects.items() if not p['closed_at']},
                                 {e: v[0] for e, v in per_event.items()})
    heat_day = collections.defaultdict(float)
    project_events = collections.defaultdict(list)
    project_people = collections.defaultdict(float)
    project_people_last = {}
    for event_id, ids, guess in [(e, ids, 0) for e, ids in spread.items()] + [(e, ids, 1) for e, ids in probable.items()]:
        at = times.get(event_id)
        if at is None:
            continue
        for pid in ids:
            project_events[pid].append((at, event_id, guess))
            if at <= current:
                # A probable one counts half until a review confirms it.
                heat_day[(pid, at.date())] += 0.1 if guess else 0.2
                for node in event_people.get(event_id, ()):
                    key = (node, pid)
                    project_people[key] += fade(0.15 if guess else 0.3, age(at.date()), HALF_LIFE['project_person'])
                    project_people_last[key] = max(project_people_last.get(key, at), at)
    unnamed = unnamed_work(per_event, event_people, owner_events, set(spread) | set(probable), vectors, current)

    # The owner's decisions on cards: once per card, for the people it was directly about.
    decided = collections.defaultdict(float)
    cards = {}
    for r in db.execute('SELECT d.suggestion_id,d.choice,d.feedback,d.created_at,s.event_ids,s.project FROM decisions d '
                        'JOIN suggestions s ON s.id=d.suggestion_id '
                        'WHERE NOT EXISTS (SELECT 1 FROM decision_annotations a WHERE a.decision_id=d.id) ORDER BY d.created_at'):
        weight = DECISION_WEIGHT.get(r['choice'])
        if weight is None or (r['choice'] == 'reject' and (r['feedback'] or '').strip() not in ('', 'Not relevant', 'Wrong facts')):
            continue  # "Already handled" says nothing against the people
        at = parse_time(r['created_at'])
        if at > current:
            continue
        card = cards.setdefault(r['suggestion_id'], {'weight': weight, 'at': at, 'event_ids': r['event_ids'], 'project': r['project']})
        if weight > card['weight'] or (weight < 0 < card['weight']):
            card.update(weight=weight, at=at)  # the strongest yes; a later Not relevant overrides it
    projects_by_name = {p['name'].casefold(): pid for pid, p in projects.items()}
    for card in cards.values():
        try:
            evidence = [int(i) for i in json.loads(card['event_ids'] or '[]')]
        except (ValueError, TypeError):
            evidence = []
        for node in {n for e in evidence for n in direct_people.get(e, ())}:
            decided[node] += fade(card['weight'], age(card['at'].date()), HALF_LIFE['person'])
        project_id = projects_by_name.get(str(card['project'] or '').casefold())
        if project_id and card['weight'] > 0:
            heat_day[(project_id, card['at'].date())] += card['weight']
    for node, weight in decided.items():
        strength[node] = max(0.0, strength[node] + weight)

    heat_days = collections.defaultdict(list)
    for (pid, day), weight in heat_day.items():
        heat_days[pid].append((dt.datetime.combine(day, dt.time(12), dt.timezone.utc), min(2.0, weight)))

    def heat(pid, then):
        return sum(fade(w, (then - at).total_seconds() / 86400, HALF_LIFE['project']) for at, w in heat_days[pid] if at <= then)

    # Write.
    nodes, links, members = [], [], collections.defaultdict(list)
    named = {r['node_id']: r['name'] for r in db.execute('SELECT node_id,name FROM graph_names')}
    role_rows = {r['identifier']: dict(r) for r in db.execute('SELECT * FROM person_roles')}
    said = {i: (r['role'] or None, 'owner' if r['source'] == 'owner' else 'review') for i, r in role_rows.items()}
    related = db.execute("SELECT a,b,relation,source FROM graph_relations ORDER BY source='owner',created_at").fetchall()
    profiles = {r['domain']: json.loads(r['profile']) for r in db.execute("SELECT domain,profile FROM org_profiles WHERE profile IS NOT NULL AND status!='wrong'")}
    wrong = {r[0] for r in db.execute("SELECT domain FROM org_profiles WHERE status='wrong'")}
    edits = {r['domain']: json.loads(r['fields']) for r in db.execute('SELECT domain,fields FROM org_edits')}
    stated_rows = {r['identifier']: dict(r) for r in db.execute('SELECT * FROM person_orgs')}
    stated = {}  # person node -> the statement of where they work, when no work address says it
    surnames = {n.split()[-1] for n in owner.names if len(n.split()) >= 2}
    for node, value in strength.items():
        if value < FLOOR:
            continue
        group = sorted(union.members.get(node, {node}))
        counts = collections.Counter()
        for identifier in group:
            counts.update(names.get(identifier, {}))
        name = counts.most_common(1)[0][0] if counts else shown_id(node, x_names)
        if plain_name(name) in owner.names:
            continue  # the owner on another channel (a note-to-self chat, an old address)
        name = next((named[i] for i in group if i in named), name)  # the owner's own name for them wins
        talk = sorted(roles[node]['owner'] | roles[node]['them'] | roles[node]['met'])
        gaps = [(b - a).days for a, b in zip(talk, talk[1:])][-8:]
        usual = sorted(gaps)[len(gaps) // 2] if len(gaps) >= 3 else None
        if talk:  # contact is with the owner: someone answering a customer or posting in a channel isn't
            last_day[node] = talk[-1]
        since = (current.astimezone(tz).date() - last_day[node]).days if node in last_day else None
        quiet = bool(usual and len(roles[node]['owner']) >= 2 and since is not None and max(14, 3 * usual) < since < 365)
        domains = sorted({i.split('@', 1)[1] for i in group if i.startswith('email:')} - FREEMAIL - owner.domains)
        said_org = max((stated_rows[i] for i in group if i in stated_rows),
                       key=lambda r: (r['source'] == 'owner', r['said_at'] or '', r['created_at'], r['identifier'] == node), default=None)
        if said_org and said_org['source'] != 'owner' and domains:
            said_org = None  # a work address says it already
        if said_org and said_org['org']:
            stated[node] = said_org
        elif domains and not said_org:
            members[domains[0]].append((node, value))
        colleague = any(i.startswith('email:') and i.rsplit('@', 1)[1] in owner.domains for i in group)
        role_id = max((i for i in group if i in role_rows), key=lambda i: (role_rows[i]['source'] == 'owner', role_rows[i].get('checked_at') or role_rows[i]['created_at'], i == node), default=None)
        role, role_by = said.get(role_id, (None, None))  # only what the owner or a review said
        full = plain_name(name)
        hints = sorted(set().union(*(appears.get(i, ()) for i in group))) + (["an address at the owner's own domain"] if colleague else []) \
            + (["in the owner's Slack"] if any(i.startswith('slack:') for i in group) else []) \
            + (["shares the owner's surname"] if full and surnames & set(full.split()[1:]) else [])
        info = {'ids': sorted({shown_id(i, x_names) for i in group}), 'org': ('org:' + domains[0]) if domains and not said_org else None,
                'org_by': 'email' if domains and not said_org else None, 'has_headline': any(i in headlines for i in group),
                'wrote': len(roles[node]['owner']), 'heard': len(roles[node]['them']), 'met': len(roles[node]['met']),
                'accounts': sorted(accounts[node]), 'next_meeting': next_meeting[node].isoformat() if node in next_meeting else None,
                'colleague': colleague, 'role': role, 'role_by': role_by, 'role_id': role_id, 'identifiers': group, 'hints': hints, 'seen_only': not talk}
        nodes.append((node, 'person', name[:200], round(value, 4), last_day[node].isoformat() if node in last_day else None,
                      len(node_events[node]), usual, int(quiet), None, info))
    # Stated organisations join the one with that name or domain, or stand as their own (no domain, never looked up).
    def org_name(domain):
        return (named.get('org:' + domain) or crm_names.get(domain) or paying_names.get(domain)
                or ({**(profiles.get(domain) or {}), **edits.get(domain, {})}).get('name') or domain)
    by_org_name = {}
    for domain in sorted(members):
        by_org_name.setdefault(folded_words(org_name(domain)), domain)
    for domain in sorted(members):  # "Acme" is acme.com when no organisation is called that
        by_org_name.setdefault(folded_words(domain.split('.')[0]), domain)
    named_orgs = collections.defaultdict(list)
    infos = {n[0]: n[9] for n in nodes}
    own = {folded_words(d.split('.')[0]) for d in owner.domains}
    for node, said_org in stated.items():
        value = strength[node]
        if folded_words(said_org['org']) in own:
            infos[node]['colleague'] = True  # the owner's own company, as with an address at the owner's domain
            continue
        given = str(said_org['org_id'] or '')
        domain = given[4:] if given.startswith('org:') and given[4:] in members else by_org_name.get(folded_words(said_org['org']))
        if domain:
            members[domain].append((node, value))
            org_id = 'org:' + domain
        else:
            org_id = 'org:named:' + '-'.join(folded_words(said_org['org']).split())[:80]
            named_orgs[org_id].append((node, value, said_org['org']))
        infos[node].update(org=org_id, org_by=said_org['source'] if said_org['source'] == 'owner' else 'review', org_name=said_org['org'],
                           org_via=said_org['via'], org_event_id=said_org['event_id'], org_at=said_org['said_at'])  # the quote stays sealed
    nodes = [n[:9] + (json.dumps(n[9]),) if isinstance(n[9], dict) else n for n in nodes]
    kept = {n[0] for n in nodes}
    for org_id, people in named_orgs.items():
        role, role_by = said.get(org_id, (None, None))
        nodes.append((org_id, 'org', (named.get(org_id) or collections.Counter(p[2] for p in people).most_common(1)[0][0])[:200],
                      round(sum(v for _, v, _ in people), 4), max((last_day[p].isoformat() for p, _, _ in people if p in last_day), default=None),
                      len(people), None, 0, None, json.dumps({'domain': None, 'role': role, 'role_by': role_by, 'paying': False, 'profile': None,
                                                              'edited': [], 'wrong': False, 'stated': True})))
        for node, value, _ in people:
            links.append((node, org_id, 'member', round(value, 4), None))
    for domain, people in members.items():
        total = sum(v for _, v in people)
        role, role_by = said.get('org:' + domain, (None, None))
        profile = {**(profiles.get(domain) or {}), **edits.get(domain, {})} or None  # what the owner wrote wins
        nodes.append(('org:' + domain, 'org', org_name(domain)[:200], round(total, 4),
                      max((last_day[p].isoformat() for p, _ in people if p in last_day), default=None), len(people), None, 0, None,
                      json.dumps({'domain': domain, 'role': role, 'role_by': role_by, 'paying': domain in paying_names, 'profile': profile,
                                  'edited': sorted(edits.get(domain, {})), 'wrong': domain in wrong})))
        for node, value in people:
            links.append((node, 'org:' + domain, 'member', round(value, 4), None))
    two_weeks = current - dt.timedelta(days=14)
    for pid, project in projects.items():
        value, before = heat(pid, current), heat(pid, two_weeks)
        if value < FLOOR and not project_events.get(pid):
            continue
        events_ = sorted(project_events.get(pid, ()))
        nodes.append(('project:' + str(pid), 'project', project['name'], round(value, 4), events_[-1][0].date().isoformat() if events_ else None,
                      len(events_), None, 0, round(value / before, 2) if before > FLOOR else None,
                      json.dumps({'closed_at': project['closed_at'], 'created_at': project['created_at'], 'probable': sum(1 for e in events_ if e[2])})))
        kept.add('project:' + str(pid))
    for (node, pid), value in project_people.items():
        if value >= FLOOR and node in kept:
            links.append((node, 'project:' + str(pid), 'project', round(value, 4), project_people_last[(node, pid)].date().isoformat()))
    pairs = collections.defaultdict(float)
    pair_last = {}
    for (a, b, day), weight in pair_day.items():
        pairs[(a, b)] += fade(weight, age(day), HALF_LIFE['pair'])
        pair_last[(a, b)] = max(pair_last.get((a, b), day), day)
    # Relations a review or the owner read in the messages ("assistant of", "sister of", "introduced by").
    relations = {}
    for r in related:
        a, b = union.find(r['a']), union.find(r['b'])
        if a != b and a in kept and b in kept:
            relations[tuple(sorted((a, b)))] = (a, b, r['relation'], 'owner' if r['source'] == 'owner' else 'review')
    for (a, b), value in pairs.items():
        if value >= FLOOR and a in kept and b in kept and (a, b) not in relations:
            links.append((a, b, 'together', round(value, 4), pair_last[(a, b)].isoformat()))
    by_node = {n[0]: i for i, n in enumerate(nodes)}
    for pair, (a, b, relation, by) in relations.items():
        if not relation:
            continue  # a deliberate removal stays removed, including after identity merges
        links.append((*pair, 'relation', round(max(0.5, pairs.get(pair, 0)), 4), pair_last[pair].isoformat() if pair in pair_last else None))
        for node in (a, b):
            row = list(nodes[by_node[node]])
            info = json.loads(row[9])
            info.setdefault('relations', []).append({'a': a, 'a_name': nodes[by_node[a]][2], 'relation': relation, 'b': b, 'b_name': nodes[by_node[b]][2], 'by': by})
            row[9] = json.dumps(info)
            nodes[by_node[node]] = tuple(row)
    kept |= {n[0] for n in nodes}
    event_rows = []
    for node, items in [(n, [(at, e, 0) for at, e in v]) for n, v in node_events.items()] + [('project:' + str(p), v) for p, v in project_events.items()]:
        if node in kept:
            # Complete membership for old task evidence; only readers cap display lists.
            # direct: the person was in contact with the owner on it, not merely on it.
            event_rows += [(node, e, at.isoformat(), guess, int(node in direct_people.get(e, ()))) for at, e, guess in sorted(items)]
    names_of = {n[0]: n[2] for n in nodes}
    unnamed_rows = []
    for group, people, last in unnamed:
        marks = ','.join('?' for _ in group)
        titles = list(dict.fromkeys(re.sub(r'^(?:(?:re|fwd?|aw|tr)\s*:\s*)+', '', r[0], flags=re.I)[:80]
                                    for r in db.execute(f'SELECT title FROM events WHERE id IN ({marks}) ORDER BY occurred_at DESC', group)))
        unnamed_rows.append((min(group), json.dumps(sorted(group)), json.dumps([names_of[p] for p in people if p in names_of][:4]),
                             json.dumps(titles[:4]), len(group), last.isoformat()))
    took = int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000)
    try:
        db.execute('DELETE FROM graph_nodes')
        db.execute('DELETE FROM graph_links')
        db.execute('DELETE FROM graph_events')
        db.execute('DELETE FROM graph_unnamed')
        db.executemany('INSERT OR REPLACE INTO graph_nodes(id,kind,name,strength,last_at,signals,gap_days,quiet,trend,info) VALUES(?,?,?,?,?,?,?,?,?,?)', nodes)
        db.executemany('INSERT OR REPLACE INTO graph_links(a,b,kind,strength,last_at) VALUES(?,?,?,?,?)', links)
        # A confirmed label wins over a probable one for the same signal.
        db.executemany('INSERT INTO graph_events(node_id,event_id,at,probable,direct) VALUES(?,?,?,?,?) '
                       'ON CONFLICT(node_id,event_id) DO UPDATE SET probable=min(probable,excluded.probable),direct=max(direct,excluded.direct)', event_rows)
        db.executemany('INSERT OR REPLACE INTO graph_unnamed(key,event_ids,people,titles,size,last_at) VALUES(?,?,?,?,?,?)', unnamed_rows)
        db.execute('INSERT OR REPLACE INTO graph_state(id,built_at,signals,took_ms) VALUES(1,?,?,?)', (now, len(signals), took))
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise
    return {'people': sum(1 for n in nodes if n[1] == 'person'), 'projects': sum(1 for n in nodes if n[1] == 'project'),
            'links': len(links), 'signals': len(signals), 'probable': len(probable), 'unnamed': len(unnamed_rows), 'took_ms': took}


# Reading.

def closeness(value):
    return 'close' if value >= 3 else 'regular' if value >= 1 else 'occasional' if value >= 0.3 else 'faint'


def node_view(row, links=None):
    info = json.loads(row['info'] or '{}')
    view = {'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'strength': round(row['strength'], 2), 'last_at': row['last_at'],
            'signals': row['signals']}
    if row['kind'] == 'person':
        view.update(closeness=closeness(row['strength']), ids=info.get('ids', []), org=info.get('org'), quiet=bool(row['quiet']),
                    usual_gap_days=row['gap_days'], wrote_days=info.get('wrote'), heard_days=info.get('heard'), met_days=info.get('met'),
                    next_meeting=info.get('next_meeting'), colleague=info.get('colleague'), role=info.get('role'),
                    role_by=info.get('role_by'), role_id=info.get('role_id'), hints=info.get('hints', []),
                    seen_only=bool(info.get('seen_only')), relations=info.get('relations', []), org_by=info.get('org_by'),
                    org_via=info.get('org_via'), org_event_id=info.get('org_event_id'), org_at=info.get('org_at'))
    elif row['kind'] == 'org':
        view.update(role=info.get('role'), role_by=info.get('role_by'), domain=info.get('domain'), profile=info.get('profile'),
                    about=enrichment.summary(info.get('profile')), edited=info.get('edited', []), wrong=bool(info.get('wrong')))
    elif row['kind'] == 'project':
        view.update(trend=row['trend'], state=project_state(row), closed=bool(info.get('closed_at')), probable_signals=info.get('probable', 0))
    if links is not None:
        view['links'] = links
    return view


def project_state(row):
    if row['strength'] < 0.3:
        return 'dormant'
    # Fourteen quiet days decay heat to ~0.63. Below 0.8 means cooling,
    # while continuing activity can offset that decay and remain active.
    if row['trend'] is not None and row['trend'] < 0.8:
        return 'cooling'
    if row['trend'] is None or row['trend'] > 1.3:
        return 'heating up'
    return 'active'


def neighbours(db, node_id, limit=12):
    rows = db.execute('SELECT l.*,n.name,n.kind node_kind FROM graph_links l JOIN graph_nodes n ON n.id=(CASE WHEN l.a=? THEN l.b ELSE l.a END) '
                      'WHERE l.a=? OR l.b=? ORDER BY l.strength DESC LIMIT ?', (node_id, node_id, node_id, limit)).fetchall()
    return [{'id': r['b'] if r['a'] == node_id else r['a'], 'name': r['name'], 'kind': r['node_kind'], 'link': r['kind'],
             'strength': round(r['strength'], 2), 'last_at': r['last_at']} for r in rows]


def excerpt(text, head=320, tail=220):
    """A message without quoted history; long ones keep their start and their end, where a signature is."""
    text = re.split(r'\[Quoted earlier messages|\n-{2,} ?(?:Original Message|Forwarded message)|\nOn .{6,80} wrote:', str(text or ''), maxsplit=1)[0]
    text = re.sub(r'[ \t]+', ' ', re.sub(r'\n\s*\n+', '\n', text)).strip()
    return text if len(text) <= head + tail + 20 else text[:head].rstrip() + ' … ' + text[-tail:].lstrip()


def person_evidence(db, node_id, owner, limit=8):
    """What a review needs to tell who someone is to the owner: their first direct contact (often an
    introduction) and the latest ones, meetings as titles only."""
    rows = db.execute('SELECT e.id,e.kind,e.occurred_at,e.title,e.body,e.metadata FROM graph_events g JOIN events e ON e.id=g.event_id '
                      'WHERE g.node_id=? AND g.direct=1 AND e.deleted=0 ORDER BY e.occurred_at', (node_id,)).fetchall()
    meetings = [r for r in rows if r['kind'] in ('calendar.event', 'salesflare.meeting')]
    messages = [r for r in rows if r not in meetings]
    picked = (messages[:1] + messages[1:][-(limit - 3):]) if messages else []
    picked += meetings[-2:]
    out = []
    for r in sorted({r['id']: r for r in picked}.values(), key=lambda r: r['occurred_at']):
        m = json.loads(r['metadata'] or '{}')
        sent = ('SENT' in (m.get('labels') or ()) or bool(m.get('from_me')) or m.get('user_id') in owner.slack
                or any(a in owner.emails for _, a in addresses(m.get('from'))[:1]))
        item = {'event_id': r['id'], 'kind': r['kind'], 'at': r['occurred_at'][:10], 'title': str(r['title'] or '')[:120]}
        if r in meetings:
            item['attendees'] = len(m.get('attendees') or m.get('contacts') or ())
        else:
            item.update({'from': 'the owner' if sent else str(m.get('from') or m.get('chat_name') or '')[:120], 'text': excerpt(r['body'])})
            if r['kind'] == 'linkedin.message' and m.get('headline') and not m.get('group'):
                item['their_linkedin_headline'] = str(m['headline'])[:200]
        out.append(item)
    return out


def find(db, text, limit=10, kinds=('person', 'project', 'org'), also=None):
    """Nodes by name, address or handle; strongest first. also: more text to match per node (a Fleet member's name)."""
    words = [w for w in re.findall(r'[\w@.+-]+', str(text or '').casefold()) if len(w) > 1]
    if not words:
        return []
    marks = ','.join('?' for _ in kinds)
    rows = db.execute(f'SELECT * FROM graph_nodes WHERE kind IN ({marks}) ORDER BY strength DESC', tuple(kinds)).fetchall()
    folded = lambda s: unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().casefold()
    also = also or {}
    hits = [r for r in rows if all(w in folded(r['name'] + ' ' + r['id'] + ' ' + r['info'] + ' ' + also.get(r['id'], '')) for w in map(folded, words))]
    return hits[:limit]


def spread(db, seeds, limit=12, hops=2):
    """Spreading activation from seed nodes: what is alive around them, by link strength."""
    activation = {s: 1.0 for s in seeds}
    frontier = dict(activation)
    for _ in range(hops):
        following = collections.defaultdict(float)
        for node, value in frontier.items():
            links = db.execute('SELECT a,b,strength FROM graph_links WHERE a=? OR b=? ORDER BY strength DESC LIMIT 40', (node, node)).fetchall()
            top = max((r['strength'] for r in links), default=0)
            for r in links:
                other = r['b'] if r['a'] == node else r['a']
                following[other] += value * 0.5 * r['strength'] / top
        frontier = {n: v for n, v in following.items() if n not in seeds}
        for node, value in frontier.items():
            activation[node] = activation.get(node, 0) + value
    ranked = sorted(((v, n) for n, v in activation.items() if n not in seeds), reverse=True)[:limit]
    rows = {r['id']: r for r in db.execute('SELECT * FROM graph_nodes WHERE id IN (' + ','.join('?' for _ in ranked) + ')', [n for _, n in ranked])} if ranked else {}
    return [{**node_view(rows[n]), 'activation': round(v, 3)} for v, n in ranked if n in rows]


def for_events(db, event_ids):
    """The people and projects on these signals, strongest first."""
    ids = [int(i) for i in event_ids]
    if not ids:
        return []
    marks = ','.join('?' for _ in ids)
    return db.execute(f'SELECT DISTINCT n.* FROM graph_events g JOIN graph_nodes n ON n.id=g.node_id WHERE g.event_id IN ({marks}) ORDER BY n.kind DESC,n.strength DESC', ids).fetchall()


def short_date(value):
    if not value:
        return 'never'
    day = dt.date.fromisoformat(str(value)[:10])
    return f'{day:%a} {day.day} {day:%b}'


def context_lines(db, event_ids, limit_chars=1500, skip_card=None):
    """A few lines for a preparation: who these people are to the owner, and the projects around them."""
    lines = []
    for row in for_events(db, event_ids)[:8]:
        view = node_view(row)
        if row['kind'] == 'person':
            projects = [n for n in neighbours(db, row['id'], 12) if n['kind'] == 'project'][:3]
            people = [n for n in neighbours(db, row['id'], 12) if n['kind'] == 'person'][:3]
            org = db.execute('SELECT name,info FROM graph_nodes WHERE id=?', (view['org'],)).fetchone() if view['org'] else None
            org_info = json.loads(org['info']) if org else {}
            org_bits = [b for b in (org_info.get('role'), enrichment.summary(org_info.get('profile'))) if b]
            bits = ([view['role']] if view['role'] else ['no role yet']) + ([f"at {org['name']}" + (f" ({'; '.join(org_bits)})" if org_bits else '')] if org else []) \
                + [relation_text(r) for r in view['relations'][:2]] \
                + [f"{view['closeness']} contact", f"last {'seen' if view['seen_only'] else 'contact'} {short_date(view['last_at'])}"]
            if view['usual_gap_days']:
                gap = int(view['usual_gap_days'])
                bits.append(('usually daily' if gap <= 1 else f'usually every {gap} days') + (' (gone quiet)' if view['quiet'] else ''))
            if view['next_meeting']:
                bits.append('next meeting ' + short_date(view['next_meeting']))
            if projects:
                bits.append('projects: ' + ', '.join(p['name'] for p in projects))
            if people:
                bits.append('often with ' + ', '.join(p['name'] for p in people))
            between = open_line(open_between(db, row['id'], 2, skip_card))
            if between:
                bits.append('open: ' + between)
            lines.append(f"- {view['name']} ({', '.join(view['ids'][:2])}): " + '; '.join(bits))
        elif row['kind'] == 'project':
            people = [n['name'] for n in neighbours(db, row['id'], 6) if n['kind'] == 'person'][:4]
            between = open_line(open_between(db, row['id'], 2, skip_card))
            lines.append(f"- Project {view['name']}: {view['state']}, last signal {short_date(view['last_at'])}" + (f"; people: {', '.join(people)}" if people else '')
                         + (f"; open: {between}" if between else ''))
    text = ''
    for line in lines:
        if len(text) + len(line) + 1 > limit_chars:
            break
        text += line + '\n'
    return text.rstrip()


def overview(db, limit=12):
    rows = lambda sql, *args: [node_view(r) for r in db.execute(sql, args)]
    state = db.execute('SELECT built_at,signals,took_ms FROM graph_state WHERE id=1').fetchone()
    week_ago = ((parse_time(state['built_at']) if state else dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=7)).date().isoformat()
    projects = rows("SELECT * FROM graph_nodes WHERE kind='project' AND json_extract(info,'$.closed_at') IS NULL ORDER BY strength DESC LIMIT ?", 30)
    for project in projects:
        project['people'] = [n['name'] for n in neighbours(db, project['id'], 8) if n['kind'] == 'person'][:4]
    return {'built_at': state['built_at'] if state else None,
            'projects': projects,
            'people': rows("SELECT * FROM graph_nodes WHERE kind='person' ORDER BY strength DESC LIMIT ?", limit),
            'quiet': rows("SELECT * FROM graph_nodes WHERE kind='person' AND quiet=1 ORDER BY strength DESC LIMIT ?", limit),
            'new': rows("SELECT * FROM graph_nodes WHERE kind='person' AND signals<=3 AND last_at>=? ORDER BY strength DESC LIMIT ?",
                        week_ago, 8),
            'orgs': rows("SELECT * FROM graph_nodes WHERE kind='org' ORDER BY strength DESC LIMIT ?", 8),
            'whos_who': unconfirmed(db),
            'waiting': waiting(db),
            'warmup': hidden_list(db)}


def headline(db, node_id):
    """A person's latest LinkedIn headline, their own words about themselves, read from their signals."""
    for (metadata,) in db.execute("SELECT e.metadata FROM graph_events g JOIN events e ON e.id=g.event_id WHERE g.node_id=? "
                                  "AND e.kind='linkedin.message' AND e.deleted=0 ORDER BY e.occurred_at DESC LIMIT 20", (node_id,)):
        try:
            m = json.loads(metadata or '{}')
        except ValueError:
            continue
        if isinstance(m.get('headline'), str) and m['headline'].strip() and not m.get('group'):
            return ' '.join(m['headline'].split())[:200]
    return None


def detail(db, node_id, events=12):
    row = db.execute('SELECT * FROM graph_nodes WHERE id=?', (node_id,)).fetchone()
    if not row:
        return None
    view = node_view(row, neighbours(db, node_id, 20))
    rows = db.execute('SELECT event_id,probable FROM graph_events WHERE node_id=? ORDER BY at DESC LIMIT ?', (node_id, events)).fetchall()
    view['event_ids'] = [r[0] for r in rows]
    view['probable_ids'] = [r[0] for r in rows if r[1]]
    view['open'] = open_between(db, node_id)
    said = db.execute('SELECT quote,event_id,created_at FROM person_roles WHERE identifier=?', (view.get('role_id') or node_id,)).fetchone()
    if said and view.get('role'):
        view.update(role_quote=said['quote'], role_event_id=said['event_id'], role_set_at=said['created_at'][:10])
    if row['kind'] == 'person':
        view['headline'] = headline(db, node_id) if json.loads(row['info'] or '{}').get('has_headline') else None
        if view.get('org'):
            org = db.execute('SELECT name FROM graph_nodes WHERE id=?', (view['org'],)).fetchone()
            view['org_name'] = org['name'] if org else None
        if view.get('org_by') == 'review':
            ids = json.loads(row['info'] or '{}').get('identifiers') or [node_id]
            said_org = db.execute(f"SELECT quote FROM person_orgs WHERE identifier IN ({','.join('?' for _ in ids)}) AND event_id=?",
                                  (*ids, view.get('org_event_id'))).fetchone()
            view['org_quote'] = said_org['quote'] if said_org else None
    if row['kind'] == 'org':
        view['people_roles'] = [{'id': r['id'], 'name': r['name'], 'role': json.loads(r['info']).get('role')} for r in db.execute(
            "SELECT n.id,n.name,n.info FROM graph_links l JOIN graph_nodes n ON n.id=l.a WHERE l.b=? AND l.kind='member' ORDER BY n.strength DESC LIMIT 12", (node_id,))]
    return view


# The owner's Fleets, Family and Friends on the graph. Only what the Network's directory already shows every member of
# that group (the name they chose to share there, their verified addresses, whether they have a Machine), matched on
# those addresses; nothing is asked of their Machine and nothing leaves this one. Read at view time from the
# directory kept here, so leaving a Fleet (or a friend removed) takes the marker and the name away with the next
# directory; a name the owner kept (graph_names) stays.
FLEET_GROUPS = {'work': 'Work Fleet', 'family': 'Family', 'friends': 'Friend'}
MEMBER_EMAIL = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')


def fleet_marks(db, fleets):
    """{node_id: {'name', 'machine', 'in': [...]}} for the people in the graph who are members of the owner's Fleets,
    Family or Friends (fleets: the directory by Fleet id, as fleet.Fleet.fleets() gives it). Members only: a pending
    friend request or a blocked person is not one."""
    by_email = collections.defaultdict(list)
    for ref, found in (fleets or {}).items():
        kind = (found or {}).get('kind')
        if kind not in FLEET_GROUPS:
            continue
        for m in found.get('members') or ():
            if not isinstance(m, dict) or m.get('me') or not m.get('member_id'):
                continue
            for e in {str(e).strip().lower() for e in m.get('emails') or () if MEMBER_EMAIL.fullmatch(str(e).strip())}:
                by_email[e].append((ref, kind, found, m))
    if not by_email:
        return {}
    marks = {}
    ids = ['email:' + e for e in by_email]
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        rows = db.execute("SELECT DISTINCT n.id,j.value FROM graph_nodes n, json_each(n.info,'$.identifiers') j WHERE n.kind='person' AND j.value IN ("
                          + ','.join('?' for _ in chunk) + ')', chunk).fetchall()
        for node_id, identifier in rows:
            mark = marks.setdefault(node_id, {'name': '', 'machine': False, 'in': []})
            for ref, kind, found, m in by_email[identifier.split(':', 1)[1]]:
                if any(x['fleet'] == ref and x['member_id'] == m['member_id'] for x in mark['in']):
                    continue
                mark['in'].append({'kind': kind, 'label': FLEET_GROUPS[kind], 'fleet': ref, 'fleet_name': str(found.get('name') or '')[:80],
                                   'member_id': str(m['member_id'])})
                mark['machine'] = mark['machine'] or bool(m.get('machine'))
                name = ' '.join(str(m.get('name') or '').split())[:100]
                if not mark['name'] or (kind == 'work' and plain_name(name)):  # the name shared in a Work Fleet first
                    mark['name'] = name if plain_name(name) else mark['name']
    order = {'work': 0, 'family': 1, 'friends': 2}
    for mark in marks.values():
        mark['in'].sort(key=lambda x: (order[x['kind']], x['fleet_name'].casefold()))
    return marks


def incomplete_name(name):
    """A name from the messages that is only part of one: a single word ("sam"), all lower case, or an address."""
    name = str(name or '').strip()
    return not name or '@' in name or plain_name(name) is None or name == name.lower()


def with_fleet(db, result, marks):
    """Marks the people in a graph view (overview, detail, search) who are in the owner's Fleets, Family or Friends, and
    gives an incomplete name ("sam") the name they share there. The owner's own name for someone always wins."""
    if not marks:
        return result
    kept = {r[0] for r in db.execute('SELECT node_id FROM graph_names')}

    def mark(item):
        found = marks.get(item.get('id'))
        if not found:
            return
        item['fleet'] = {'machine': found['machine'], 'in': found['in'], 'name': found['name'] or None}
        row = db.execute('SELECT info FROM graph_nodes WHERE id=?', (item['id'],)).fetchone()
        group = set(json.loads(row['info']).get('identifiers') or ()) | {item['id']} if row else {item['id']}
        if found['name'] and not (group & kept) and incomplete_name(item.get('name')) and found['name'] != item.get('name'):
            item['name_from_messages'], item['name'] = item.get('name'), found['name']
            item['fleet']['named'] = True

    def walk(value):
        if isinstance(value, dict):
            if value.get('kind') == 'person' and isinstance(value.get('id'), str):
                mark(value)
            for v in value.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    walk(result)
    return result


def open_between(db, node_id, limit=6, skip_card=None):
    """What is open between the owner and this person or project: promises both ways and open cards, read from the
    signals each sits on. Apart from that, under known: the facts the owner told that are about them (fact_about).
    A fact is about its subject, never about whoever sent the message it came up in: one about the owner is on no page."""
    out = {'you_promised': [], 'they_promised': [], 'cards': [], 'known': []}
    row = db.execute("SELECT kind,name,info FROM graph_nodes WHERE id=?", (node_id,)).fetchone()
    project = row if row and row['kind'] == 'project' else None
    ids = list(dict.fromkeys([node_id, *((json.loads(row['info'] or '{}').get('identifiers') or []) if row else [])]))
    on_node = 'SELECT 1 FROM json_each({}) j JOIN graph_events g ON g.event_id=j.value WHERE g.node_id=?'
    try:
        for r in db.execute("SELECT p.id,p.direction,p.who,p.what,p.due FROM promises p LEFT JOIN suggestions s ON s.id=p.suggestion_id "
                            "WHERE (p.status='open' OR (p.status='carded' AND s.status IN ('pending','accepted','prepared','instructions','snoozed'))) "
                            "AND p.event_id IN (SELECT event_id FROM graph_events WHERE node_id=?) ORDER BY coalesce(p.due,p.check_at) LIMIT ?", (node_id, limit * 2)):
            out['you_promised' if r['direction'] == 'mine' else 'they_promised'].append({'id': r['id'], 'who': r['who'], 'what': r['what'], 'due': r['due']})
        marks = ','.join('?' for _ in OPEN_CARD)
        out['cards'] = [dict(r) for r in db.execute(
            f"SELECT s.id,s.title,s.status,s.due FROM suggestions s WHERE s.status IN ({marks}) AND s.id IS NOT ? AND (EXISTS ({on_node.format('s.event_ids')}) "
            "OR (? IS NOT NULL AND s.project=? COLLATE NOCASE)) ORDER BY coalesce(s.due,s.created_at) LIMIT ?",
            (*OPEN_CARD, skip_card, node_id, project and project['name'], project and project['name'], limit))]
        if row and row['kind'] == 'org':  # a card the owner made about this organisation (New card, Who)
            mine = [m.get('card_id') for m in (json.loads(r['metadata'] or '{}') for r in db.execute("SELECT metadata FROM events WHERE kind='owner.card' AND deleted=0"))
                    if any(isinstance(a, dict) and a.get('id') == node_id for a in m.get('about') or ())]
            have = {c['id'] for c in out['cards']}
            out['cards'] += [dict(r) for r in db.execute(
                f"SELECT id,title,status,due FROM suggestions WHERE status IN ({marks}) AND id IS NOT ? AND id IN ({','.join('?' * len(mine)) or 'NULL'}) "
                "ORDER BY coalesce(due,created_at)", (*OPEN_CARD, skip_card, *mine)) if r['id'] not in have][:max(0, limit - len(out['cards']))]
        marks = ','.join('?' for _ in ids)
        out['known'] = [{'id': r['id'], 'text': r['text']} for r in db.execute(
            f"SELECT f.id,f.text FROM facts f WHERE f.removed_at IS NULL AND f.kind='fact' "
            f"AND EXISTS (SELECT 1 FROM fact_about a WHERE a.fact_id=f.id AND a.removed_at IS NULL AND a.node_id IN ({marks})) "
            f"AND NOT EXISTS (SELECT 1 FROM fact_about a WHERE a.fact_id=f.id AND a.removed_at IS NOT NULL AND a.node_id IN ({marks})) "
            "ORDER BY f.created_at DESC LIMIT ?", (*ids, *ids, limit))]
    except sqlite3.OperationalError:
        pass  # a bare graph without the assistant's tables
    out['you_promised'], out['they_promised'] = out['you_promised'][:limit], out['they_promised'][:limit]
    return {k: v for k, v in out.items() if v}


def open_line(items):
    """One short line from open_between, for a preparation or review."""
    bits = []
    for p in items.get('you_promised', ())[:2]:
        bits.append(f"you promised to {p['what']}" + (f" by {short_date(p['due'])}" if p['due'] else ''))
    for p in items.get('they_promised', ())[:2]:
        bits.append(f"they promised to {p['what']}" + (f" by {short_date(p['due'])}" if p['due'] else ''))
    bits += [f"card \"{c['title']}\"" for c in items.get('cards', ())[:2]]
    bits += [f"known: {f['text']}" for f in items.get('known', ())[:2]]
    return '; '.join(bits)


def card_people(db, event_ids, limit=3):
    """Who a card is directly about, closest first, with their role, and a weight to rank cards by: how close
    they are to the owner, more when their organisation pays the owner (billed through Stripe)."""
    ids = [int(i) for i in event_ids or ()]
    if not ids:
        return [], 0.0
    marks = ','.join('?' for _ in ids)
    rows = db.execute(f"SELECT DISTINCT n.id,n.name,n.strength,n.info,o.info org_info FROM graph_events g JOIN graph_nodes n ON n.id=g.node_id "
                      f"LEFT JOIN graph_nodes o ON o.id=json_extract(n.info,'$.org') WHERE g.direct=1 AND n.kind='person' AND g.event_id IN ({marks}) "
                      "ORDER BY n.strength DESC", ids).fetchall()
    people = [{'id': r['id'], 'name': r['name'], 'role': json.loads(r['info']).get('role'),
               'paying': bool(r['org_info'] and json.loads(r['org_info']).get('paying'))} for r in rows]
    weight = max((r['strength'] + (2 if p['paying'] else 0) for r, p in zip(rows, people)), default=0.0)
    return people[:limit], round(weight, 3)


def relation_text(r):
    return f"{r['a_name']} is {r['relation']} {r['b_name']}"


def projects_for(db, event_ids):
    """{event_id: [(project name, probable)]} for a batch, so a review can confirm or correct."""
    ids = [int(i) for i in event_ids]
    if not ids:
        return {}
    out = collections.defaultdict(list)
    marks = ','.join('?' for _ in ids)
    for r in db.execute(f"SELECT g.event_id,n.name,g.probable FROM graph_events g JOIN graph_nodes n ON n.id=g.node_id "
                        f"WHERE n.kind='project' AND g.event_id IN ({marks}) ORDER BY g.probable,n.strength DESC", ids):
        out[r[0]].append((r[1], bool(r[2])))
    return dict(out)


def set_role(db, node_id, role, source, now, event_id=None, quote=None):
    """Say what someone (or an organisation) is to the owner ("accountant", "Ann's partner", "customer at
    Globex", "accounting firm"). A review gives the message it read it in (event_id) and the line that
    shows it (quote, words from that message), so the owner can confirm it. The owner's word stays until
    the owner changes it; the owner saying the same role confirms it; an empty role from the owner
    clears it. When the owner replaces a review's role, the correction is kept for later reviews to learn from."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    row = db.execute("SELECT id,kind,name,info FROM graph_nodes WHERE id=? AND kind IN ('person','org')", (str(node_id),)).fetchone()
    if not row:
        raise ValueError('role needs a person or organisation node_id from graph results')
    role = ' '.join(str(role or '').split())
    if len(role) > 60 or (not role and source != 'owner'):
        raise ValueError('A role is a few words (up to 60 characters): "accountant", "investor", "customer at Globex", "accounting firm"')
    info = json.loads(row['info'] or '{}')
    identities = info.get('identifiers') or [row['id']]
    current = db.execute('SELECT * FROM person_roles WHERE identifier IN (' + ','.join('?' for _ in identities) + ') '
                         "ORDER BY source='owner' DESC,coalesce(checked_at,created_at) DESC,identifier=? DESC LIMIT 1", (*identities, row['id'])).fetchone()
    if current and current['source'] == 'owner' and source != 'owner':
        return {'role': current['role'] or None, 'kept': 'The owner set this role; it stays.'}
    quote = ' '.join(str(quote or '').split())[:200] or None
    if source != 'owner' and event_id is not None:
        members = [row['id']] if row['kind'] == 'person' else [r[0] for r in db.execute("SELECT a FROM graph_links WHERE b=? AND kind='member'", (row['id'],))]
        marks = ','.join('?' for _ in members)
        found = db.execute(f'SELECT e.title,e.body FROM graph_events g JOIN events e ON e.id=g.event_id WHERE g.event_id=? AND g.node_id IN ({marks})',
                           (int(event_id), *members)).fetchone()
        if not found:
            raise ValueError('event_id must be one of their own signals (see graph node_id)')
        if quote and folded_words(quote.strip('"\'…. ')) not in folded_words(f"{found['title']} {found['body']}"):
            raise ValueError('quote must be words copied from that message (a short line: their title, a signature, an introduction)')
    if not role:
        db.execute('INSERT OR REPLACE INTO person_roles(identifier,role,source,created_at,checked_at) VALUES(?,?,?, ?,?)', (row['id'], '', 'owner', now, now))
    elif current and current['identifier'] == row['id'] and current['role'].casefold() == role.casefold():  # confirmed by the owner, or read again by a review
        db.execute('UPDATE person_roles SET source=?,checked_at=?,event_id=coalesce(?,event_id),quote=coalesce(?,quote),hints=? WHERE identifier=?',
                   (source if source == 'owner' else current['source'], now, event_id, quote, json.dumps(json.loads(row['info']).get('hints', [])), row['id']))
    else:
        db.execute('INSERT OR REPLACE INTO person_roles(identifier,role,source,created_at,event_id,quote,hints,checked_at) VALUES(?,?,?,?,?,?,?,?)',
                   (row['id'], role, source, now, event_id if source != 'owner' else current['event_id'] if current and current['role'].casefold() == role.casefold() else None,
                    quote if source != 'owner' else current['quote'] if current and current['role'].casefold() == role.casefold() else None,
                    json.dumps(json.loads(row['info']).get('hints', [])), now))
    if source == 'owner' and current and current['source'] != 'owner' and current['role'].casefold() != role.casefold():
        db.execute('INSERT INTO role_corrections(identifier,name,old_role,new_role,created_at) VALUES(?,?,?,?,?)',
                   (row['id'], row['name'], current['role'], role, now))
    return {'role': role or None}


VIA = {'linkedin.message': 'LinkedIn', 'gmail.message': 'email', 'salesflare.email': 'email', 'calendar.event': 'calendar', 'slack.message': 'Slack',
       'whatsapp.message': 'WhatsApp', 'x.dm': 'X', 'x.mention': 'X', 'intercom.conversation': 'Intercom', 'github.notification': 'GitHub'}


def set_org(db, node_id, org, source, now, event_id=None, quote=None):
    """Say where someone works, only as a source states it: their LinkedIn headline, an email signature, them
    saying so ("I'm on the API team at Acme"). Never from who they talk to or what about. A review gives the
    signal (event_id) and the words that say it (quote, copied from that message or its sender's headline);
    both are kept with where and when it was said. org is a name or an org:… node_id. The owner's word stays
    until the owner changes it; an empty org from the owner says they have none to show."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    row = db.execute("SELECT id,name,info FROM graph_nodes WHERE id=? AND kind='person'", (str(node_id or ''),)).fetchone()
    if not row:
        raise ValueError('org goes with a person node_id from graph results')
    org, org_id = ' '.join(str(org or '').split()), None
    if org.startswith('org:'):
        found = db.execute("SELECT id,name FROM graph_nodes WHERE id=? AND kind='org'", (org,)).fetchone()
        if not found:
            raise ValueError('Unknown organisation node_id; give its name instead')
        org, org_id = found['name'], found['id']
    if len(org) > 100 or (not org and source != 'owner'):
        raise ValueError('org is the organisation\'s name as the source states it ("Acme"), up to 100 characters, or an org:… node_id')
    current = db.execute('SELECT * FROM person_orgs WHERE identifier=?', (row['id'],)).fetchone()
    if current and current['source'] == 'owner' and source != 'owner':
        return {'org': current['org'] or None, 'kept': 'The owner set this organisation; it stays.'}
    quote = ' '.join(str(quote or '').split())[:200] or None
    via = said_at = None
    if source != 'owner':
        if event_id is None or not quote:
            raise ValueError('org needs event_id and quote: the signal that states it and the words that say it')
        found = db.execute('SELECT e.kind,e.title,e.body,e.metadata,e.occurred_at FROM graph_events g JOIN events e ON e.id=g.event_id '
                           'WHERE g.event_id=? AND g.node_id=?', (int(event_id), row['id'])).fetchone()
        if not found:
            raise ValueError('event_id must be one of their own signals (see graph node_id)')
        try:
            meta = json.loads(found['metadata'] or '{}')
        except ValueError:
            meta = {}
        headline = str(meta.get('headline') or '') if found['kind'] == 'linkedin.message' and not meta.get('group') else ''
        words = folded_words(quote.strip('"\'…. '))
        if words and words in folded_words(headline):
            via = 'LinkedIn headline'
        elif meta.get('from_me') is True or 'SENT' in (meta.get('labels') or ()):
            raise ValueError('quote their own words (or their LinkedIn headline), not the owner\'s message')
        elif words and words in folded_words(f"{found['title']} {found['body']}"):
            via = VIA.get(found['kind'], found['kind'].split('.')[0])
        else:
            raise ValueError('quote must be words copied from that message, or from their LinkedIn headline on it')
        said_at = str(found['occurred_at'])
    db.execute('INSERT OR REPLACE INTO person_orgs(identifier,org,org_id,source,via,event_id,quote,said_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
               (row['id'], org, org_id, source, via, event_id if source != 'owner' else None, quote if source != 'owner' else None,
                said_at or now, now))
    return {'org': org or None}


def hidden(db):
    """Domains whose mail is an email warm-up tool's traffic, kept out of the graph."""
    return {r[0] for r in db.execute('SELECT domain FROM hidden_domains WHERE hidden=1')}


def hidden_list(db):
    return [dict(r) for r in db.execute('SELECT domain,name,source,created_at FROM hidden_domains WHERE hidden=1 ORDER BY created_at DESC,domain')]


def hide_warmup(db, node_id, warmup, source, now, event_id=None):
    """Say an organisation's mail is an email warm-up tool's traffic (templated exchanges between inboxes that
    keep a sender's reputation up, with no real conversation behind them): its domain and its people leave the
    graph, and it isn't looked up. A review judges it from what the messages are; the owner's word stays, so an
    owner's undo is never hidden again by a review. The owner can undo with warmup false and the domain or node_id."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    node_id = str(node_id or '')
    domain = node_id[4:] if node_id.startswith('org:') else node_id
    domain = domain.strip().lower()
    row = db.execute("SELECT name FROM graph_nodes WHERE id=? AND kind='org'", ('org:' + domain,)).fetchone()
    current = db.execute('SELECT * FROM hidden_domains WHERE domain=?', (domain,)).fetchone()
    if (not row and not (source == 'owner' and current)) or domain.startswith('named:'):
        raise ValueError('warmup needs an organisation node_id (org:…) with an email domain from graph results')
    if current and current['source'] == 'owner' and source != 'owner':
        return {'warmup': bool(current['hidden']), 'kept': 'The owner decided about this organisation; it stays.'}
    if source != 'owner' and event_id is not None:
        members = [r[0] for r in db.execute("SELECT a FROM graph_links WHERE b=? AND kind='member'", ('org:' + domain,))]
        if not db.execute(f"SELECT 1 FROM graph_events WHERE event_id=? AND node_id IN ({','.join('?' for _ in members) or 'NULL'})",
                          (int(event_id), *members)).fetchone():
            raise ValueError('event_id must be one of its people\'s signals (see graph node_id)')
    db.execute('INSERT OR REPLACE INTO hidden_domains(domain,hidden,source,event_id,name,created_at) VALUES(?,?,?,?,?,?)',
               (domain, int(bool(warmup)), source, event_id, row['name'] if row else current['name'], now))
    return {'warmup': bool(warmup), 'domain': domain}


def folded_words(text):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().casefold()))


def relate(db, a, b, relation, source, now, event_id=None):
    """Say how two people are related, read so that "<a> is <relation> <b>": "assistant of", "sister of",
    "introduced by", "manager of". The owner's word stays; an empty relation removes it."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    a, b = str(a or ''), str(b or '')
    found = {r['id'] for r in db.execute("SELECT id FROM graph_nodes WHERE kind='person' AND id IN (?,?)", (a, b))}
    if a == b or found != {a, b}:
        raise ValueError('related_to needs two different person node_ids from graph results')
    relation = ' '.join(str(relation or '').split())
    if len(relation) > 40 or (not relation and source != 'owner'):
        raise ValueError('A relation reads "<node_id> is <relation> <related_to>", in a few words: "assistant of", "sister of", "introduced by"')
    aliases = {}
    for identifier in (a, b):
        info = json.loads(db.execute('SELECT info FROM graph_nodes WHERE id=?', (identifier,)).fetchone()[0] or '{}')
        aliases[identifier] = info.get('identifiers') or [identifier]
    am, bm = ','.join('?' for _ in aliases[a]), ','.join('?' for _ in aliases[b])
    current = db.execute(f"SELECT source FROM graph_relations WHERE (a IN ({am}) AND b IN ({bm})) OR (a IN ({bm}) AND b IN ({am})) "
                         "ORDER BY source='owner' DESC,created_at DESC LIMIT 1", (*aliases[a], *aliases[b], *aliases[b], *aliases[a])).fetchone()
    if current and current['source'] == 'owner' and source != 'owner':
        return {'kept': 'The owner set how they are related; it stays.'}
    db.execute('DELETE FROM graph_relations WHERE (a=? AND b=?) OR (a=? AND b=?)', (a, b, b, a))
    if relation or source == 'owner':
        db.execute('INSERT INTO graph_relations(a,b,relation,source,event_id,created_at) VALUES(?,?,?,?,?,?)', (a, b, relation, source, event_id, now))
    return {'relation': relation or None}


def corrections(db, limit=8):
    """The owner's recent corrections of review roles, newest first, so later reviews learn from them."""
    return [dict(r) for r in db.execute('SELECT name,old_role,new_role,created_at FROM role_corrections ORDER BY created_at DESC LIMIT ?', (limit,))]


def corrections_lines(db, limit=8):
    return '\n'.join(f"- {c['name']}: a review said \"{c['old_role']}\", the owner " + (f"made it \"{c['new_role']}\"" if c['new_role'] else 'removed it')
                     for c in corrections(db, limit))


def unconfirmed(db, limit=30):
    """Roles a review set and the owner hasn't confirmed yet, closest first, each with the line it came from."""
    rows = db.execute("SELECT n.id identifier,r.role,r.quote,r.event_id,r.created_at,n.kind,n.name,n.strength FROM person_roles r "
                      "JOIN graph_nodes n ON r.identifier=coalesce(json_extract(n.info,'$.role_id'),n.id) WHERE r.source!='owner' ORDER BY n.kind='org',n.strength DESC LIMIT ?", (limit,)).fetchall()
    return [{'id': r['identifier'], 'kind': r['kind'], 'name': r['name'], 'role': r['role'], 'quote': r['quote'], 'event_id': r['event_id'],
             'set_at': r['created_at'][:10], 'closeness': closeness(r['strength']) if r['kind'] == 'person' else None} for r in rows]


def waiting(db):
    """How many of the review's roles wait for the owner: the Who's who count, for the Graph tab's badge."""
    return db.execute("SELECT count(*) FROM person_roles r JOIN graph_nodes n ON r.identifier=coalesce(json_extract(n.info,'$.role_id'),n.id) "
                      "WHERE r.source!='owner'").fetchone()[0]


def stale_roles(db, now, limit=3, days=RECHECK_DAYS):
    """A review's roles to read again: older than RECHECK_DAYS with contact since, with new hints since it
    was set (now billed through Stripe, a new organisation), or saved without the line it came from. The
    owner's own roles are never rechecked."""
    current = parse_time(now)
    out = []
    for r in db.execute("SELECT r.*,n.id effective_id,n.name,n.info,n.last_at,n.strength FROM person_roles r JOIN graph_nodes n ON r.identifier=coalesce(json_extract(n.info,'$.role_id'),n.id) "
                        "WHERE r.source!='owner' ORDER BY n.strength DESC"):
        checked = r['checked_at'] or r['created_at']
        then = json.loads(r['hints']) if r['hints'] else None
        new = sorted(set(json.loads(r['info']).get('hints', [])) - set(then)) if then is not None else []
        old = (current - parse_time(checked)).days >= days and (r['last_at'] or '') > checked[:10]
        if new or old or not r['quote']:
            out.append({'id': r['effective_id'], 'name': r['name'], 'role': r['role'], 'quote': r['quote'], 'set_at': checked[:10],
                        'why': ('new since: ' + ', '.join(new)) if new else f'read {(current - parse_time(checked)).days} days ago, in touch since' if old
                        else 'saved without the line it came from'})
        if len(out) >= limit:
            break
    return out


def rename(db, node_id, name, now):
    """The owner's name for a person, organisation or project. A project renamed to another project's
    name joins it; an empty name gives a person or organisation back the name from their messages."""
    node_id, name = str(node_id or ''), ' '.join(str(name or '').split())
    if node_id.startswith('project:'):
        row = db.execute('SELECT id,name FROM projects WHERE id=?', (node_id.split(':', 1)[1],)).fetchone()
        if not row:
            raise ValueError('Unknown project')
        if not name or len(name) > 60:
            raise ValueError('A project name is a short label (up to 60 characters), like "Acme VAT" or "Q4 board deck"')
        other = db.execute('SELECT id,name FROM projects WHERE name=? COLLATE NOCASE AND id!=?', (name, row['id'])).fetchone()
        try:  # cards keep their project by name
            db.execute('UPDATE suggestions SET project=? WHERE project=? COLLATE NOCASE', (other['name'] if other else name, row['name']))
        except sqlite3.OperationalError:
            pass
        if not other:
            db.execute('UPDATE projects SET name=? WHERE id=?', (name, row['id']))
            return {'node_id': node_id, 'name': name}
        db.execute('UPDATE OR IGNORE event_projects SET project_id=? WHERE project_id=?', (other['id'], row['id']))
        db.execute('DELETE FROM event_projects WHERE project_id=?', (row['id'],))
        db.execute('DELETE FROM projects WHERE id=?', (row['id'],))
        return {'node_id': 'project:' + str(other['id']), 'name': other['name'], 'merged': True}
    if not db.execute("SELECT 1 FROM graph_nodes WHERE id=? AND kind IN ('person','org')", (node_id,)).fetchone():
        raise ValueError('Rename a person, organisation or project from graph results')
    if len(name) > 100:
        raise ValueError('A name is up to 100 characters')
    if name:
        db.execute('INSERT OR REPLACE INTO graph_names(node_id,name,created_at) VALUES(?,?,?)', (node_id, name, now))
    else:
        db.execute('DELETE FROM graph_names WHERE node_id=?', (node_id,))
    return {'node_id': node_id, 'name': name or None}


ORG_FIELDS = {'website': 200, 'linkedin_url': 200, 'industry': 120, 'size': 40, 'location': 120, 'founded': 4, 'about': 600}


def org_domain(db, node_id):
    row = db.execute("SELECT info FROM graph_nodes WHERE id=? AND kind='org'", (str(node_id or ''),)).fetchone()
    if not row or not json.loads(row['info']).get('domain'):
        raise ValueError('Edit an organisation from graph results')
    return json.loads(row['info'])['domain']


def org_edit(db, node_id, fields, now):
    """The owner's own details for an organisation. They win over what an enrichment service says and survive
    every refresh; an empty field gives that one back to the service."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    domain, fields = org_domain(db, node_id), fields if isinstance(fields, dict) else {}
    row = db.execute('SELECT fields FROM org_edits WHERE domain=?', (domain,)).fetchone()
    kept = json.loads(row['fields']) if row else {}
    for field, limit in ORG_FIELDS.items():
        if field not in fields:
            continue
        text = ' '.join(str(fields[field] or '').split())
        if field == 'founded' and text:
            if not re.fullmatch(r'1[5-9]\d\d|20\d\d', text):
                raise ValueError('Founded is a year, like 2014')
            text = int(text)
        elif len(text) > limit:
            raise ValueError(f"{field.replace('_url', '').replace('_', ' ').capitalize()} is up to {limit} characters")
        elif field in ('website', 'linkedin_url') and text and not re.match(r'https?://', text):
            text = 'https://' + text
        if text:
            kept[field] = text
        else:
            kept.pop(field, None)
    if kept:
        db.execute('INSERT OR REPLACE INTO org_edits(domain,fields,updated_at) VALUES(?,?,?)', (domain, json.dumps(kept), now))
    else:
        db.execute('DELETE FROM org_edits WHERE domain=?', (domain,))
    return {'node_id': 'org:' + domain, 'edited': sorted(kept)}


def org_wrong(db, node_id, wrong, now):
    """The enrichment service matched the wrong company: its details go and the domain isn't looked up again,
    until the owner asks for a new look-up."""
    domain = org_domain(db, node_id)
    if wrong:
        db.execute("INSERT INTO org_profiles(domain,status,profile,fetched_at) VALUES(?,'wrong',NULL,?) "
                   "ON CONFLICT(domain) DO UPDATE SET status='wrong',profile=NULL,fetched_at=excluded.fetched_at", (domain, now))
    else:
        db.execute("DELETE FROM org_profiles WHERE domain=? AND status='wrong'", (domain,))
    return {'node_id': 'org:' + domain, 'wrong': bool(wrong)}


# Writing labels and merges.

def project_id(db, name, now):
    name = re.sub(r'\s+', ' ', str(name or '')).strip()
    if not name or len(name) > 60:
        raise ValueError('A project name is a short label (up to 60 characters), like "Acme VAT" or "Q4 board deck"')
    row = db.execute('SELECT id,closed_at FROM projects WHERE name=? COLLATE NOCASE', (name,)).fetchone()
    if row:
        if row['closed_at']:
            db.execute('UPDATE projects SET closed_at=NULL WHERE id=?', (row['id'],))  # it came back
        return row['id']
    return db.execute('INSERT INTO projects(name,created_at) VALUES(?,?)', (name, now)).lastrowid


def label(db, event_ids, name, source, now):
    pid = project_id(db, name, now)
    db.executemany('INSERT OR IGNORE INTO event_projects(event_id,project_id,source,created_at) VALUES(?,?,?,?)',
                   [(int(e), pid, source, now) for e in event_ids])
    return pid


def active_projects(db, limit=40):
    """Project names to reuse: the graph's live ones first, then recently named ones it hasn't seen yet."""
    names = [r[0] for r in db.execute("SELECT name FROM graph_nodes WHERE kind='project' AND strength>=? AND json_extract(info,'$.closed_at') IS NULL ORDER BY strength DESC LIMIT ?", (0.1, limit))]
    names += [r[0] for r in db.execute('SELECT name FROM projects WHERE closed_at IS NULL ORDER BY created_at DESC LIMIT ?', (limit,)) if r[0] not in names]
    return names[:limit]


def merge(db, a, b, same, now):
    a, b = sorted((str(a), str(b)))
    if a == b or not all(re.fullmatch(r'(?:email|wa|slack|li|x|gh):\S+', i) for i in (a, b)):
        raise ValueError('Merge two different person ids from graph results (like email:… or wa:…)')
    db.execute('INSERT OR REPLACE INTO graph_merges(a,b,same,created_at) VALUES(?,?,?,?)', (a, b, int(bool(same)), now))
