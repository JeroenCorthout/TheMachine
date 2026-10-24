"""People and projects: a living graph over the collected signals.

Rebuilt from scratch every few minutes as a pure function of the signals, card decisions, project
labels and merges, so nothing drifts and a rule change applies to the past too. Every interaction
adds weight that fades by half per half-life (closeness 60 days, people seen together 45, a
person's part in a project 45, a project's heat 21); a day counts at most once per person, so a
burst of chat messages is one day of contact. The owner's own messages count double theirs, a
meeting more, a newsletter or mass mailing not at all. Card decisions teach it too: Do it, Send
and Mark done strengthen the people on the evidence, Not relevant weakens them.

People are matched by identifiers only (email address, phone, Slack user, LinkedIn or X
conversation, GitHub login), joined on strong evidence only: Slack reports the user's email, the same
full name is on two identities in one message or conversation, a lookup found the same LinkedIn
profile for both, their WhatsApp number is in their own mail, the same full name appears on two
different channels (one person with it per channel), or the owner or agent says so (merges). Weaker
evidence (that name shared by two people on one channel, a similar name with the same company, people,
project or role) is only offered as "Same person?" (graph_same); the owner's Merge or Not the same is
kept in graph_merges, and Not the same also undoes a merge for good. Projects are named
by the review agent and the owner, and spread to the rest of their threads; a signal that reads like
a labeled one and shares a person with it is probably part of it too (weaker, until a review
confirms). Threads that read alike and share people, but nobody named, are offered to a review as
possibly one piece of work. People and organizations get a role (what they are to the owner), said
by the owner or by a review that read it in a message (kept with the line it came from, so the owner
can confirm it); the sources only give hints. A person's organization comes from their work email domain, or
from a source that states it plainly (their LinkedIn headline, an email signature, them saying where they work),
read by a review and kept with that line, where and when it was said; nothing is guessed. People can be related to each other ("assistant of",
"introduced by"). Going quiet compares the silence with that person's own usual gap. Messages are
never deleted; links that faded away are just not stored."""
from __future__ import annotations

import collections
import datetime as dt
import email.utils
import hashlib
import hmac
import json
import math
import operator
import os
import re
import secrets
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
CREATE TABLE IF NOT EXISTS graph_same(a TEXT NOT NULL,b TEXT NOT NULL,why TEXT NOT NULL,score REAL NOT NULL,PRIMARY KEY(a,b));
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
CREATE TABLE IF NOT EXISTS person_profiles(identifier TEXT PRIMARY KEY,status TEXT NOT NULL,profile TEXT,fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS person_photos(identifier TEXT PRIMARY KEY,key TEXT NOT NULL UNIQUE,type TEXT NOT NULL,data BLOB NOT NULL,saved_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS person_photo_tries(identifier TEXT PRIMARY KEY,tried_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contact_photo_links(identifier TEXT PRIMARY KEY,url TEXT NOT NULL,chosen INTEGER NOT NULL,source TEXT NOT NULL,seen_at TEXT NOT NULL,tried_at TEXT);
CREATE TABLE IF NOT EXISTS person_facts(identifier TEXT NOT NULL,field TEXT NOT NULL,value TEXT NOT NULL,rank INTEGER NOT NULL,source TEXT NOT NULL,seen_at TEXT NOT NULL,PRIMARY KEY(identifier,field));
CREATE TABLE IF NOT EXISTS enrich_prefs(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS forgotten_people(digest TEXT PRIMARY KEY,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS forgotten_salt(id INTEGER PRIMARY KEY CHECK(id=1),salt TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_relations(a TEXT NOT NULL,b TEXT NOT NULL,relation TEXT NOT NULL,source TEXT NOT NULL,event_id INTEGER,
  created_at TEXT NOT NULL,PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS org_edits(domain TEXT PRIMARY KEY,fields TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS person_edits(identifier TEXT PRIMARY KEY,fields TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS manual_nodes(identifier TEXT PRIMARY KEY,kind TEXT NOT NULL,name TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hidden_domains(domain TEXT PRIMARY KEY,hidden INTEGER NOT NULL,source TEXT NOT NULL,event_id INTEGER,
  name TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS person_orgs(identifier TEXT PRIMARY KEY,org TEXT NOT NULL,org_id TEXT,source TEXT NOT NULL,via TEXT,event_id INTEGER,
  quote TEXT,said_at TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_org_asks(identifier TEXT PRIMARY KEY,run_id TEXT,asked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS boards(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE COLLATE NOCASE,stages TEXT NOT NULL,source TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_people(project_id INTEGER NOT NULL,node_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(project_id,node_id));
CREATE TABLE IF NOT EXISTS board_fits(board_id INTEGER NOT NULL,project_id INTEGER NOT NULL,fit TEXT NOT NULL,why TEXT,run_id TEXT,state TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,decided_at TEXT,PRIMARY KEY(board_id,project_id));
CREATE TABLE IF NOT EXISTS project_moves(id INTEGER PRIMARY KEY,project_id INTEGER NOT NULL,board_id INTEGER,stage TEXT NOT NULL,reason TEXT,
  event_id INTEGER,source TEXT NOT NULL,at TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS project_moves_project ON project_moves(project_id,at);
CREATE TABLE IF NOT EXISTS board_offers(id INTEGER PRIMARY KEY CHECK(id=1),run_id TEXT,offered_at TEXT NOT NULL,answer TEXT);
CREATE TABLE IF NOT EXISTS board_suggestions(id INTEGER PRIMARY KEY,name TEXT NOT NULL,stages TEXT NOT NULL,about TEXT,projects TEXT NOT NULL,
  reason TEXT,source TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'open',board_id INTEGER,created_at TEXT NOT NULL,decided_at TEXT);
CREATE TABLE IF NOT EXISTS board_backfills(board_id INTEGER PRIMARY KEY,state TEXT NOT NULL DEFAULT 'running',started_at TEXT NOT NULL,
  finished_at TEXT,ranked TEXT);
CREATE TABLE IF NOT EXISTS board_backfill_asks(board_id INTEGER NOT NULL,key TEXT NOT NULL,name TEXT NOT NULL,event_ids TEXT NOT NULL,
  fit TEXT NOT NULL,why TEXT,run_id TEXT,state TEXT NOT NULL DEFAULT 'open',project_id INTEGER,created_at TEXT NOT NULL,decided_at TEXT,
  PRIMARY KEY(board_id,key));
CREATE TABLE IF NOT EXISTS board_fit_undo(token TEXT PRIMARY KEY,board_id INTEGER NOT NULL,steps TEXT NOT NULL,created_at TEXT NOT NULL);
'''
RECHECK_DAYS = 90       # a review's role is read again after this, when there was contact since
HALF_LIFE = {'person': 60, 'pair': 45, 'project_person': 45, 'project': 21}
DAY_CAP = 1.0           # one day of contact, however many messages
FLOOR = 0.01            # faded below this: not stored
HORIZON_DAYS = 400      # older signals have faded far below the floor anyway
EVENTS_PER_NODE = 40
BULK_LABELS = {'CATEGORY_PROMOTIONS', 'CATEGORY_SOCIAL', 'CATEGORY_FORUMS', 'CATEGORY_UPDATES'}
BULK = re.compile(r'(?:^|[._+-])(?:no[._-]?reply|do[._-]?not[._-]?reply|notifications?|notify|newsletters?|news|mailer-daemon|mailer|bounces?|postmaster|'
                  r'marketing|digest|alerts?|automated|receipts?|invoices?|billing)(?:[._+-]|@)|@(?:[\w-]+\.)*(?:mcsv\.net|mcdlv\.net|'
                  r'sendgrid\.net|amazonses\.com|mailgun\.org|bounces?\.[\w.-]+|calendar\.google\.com|resource\.calendar\.google\.com)$', re.I)
FREEMAIL = {'gmail.com', 'googlemail.com', 'hotmail.com', 'hotmail.be', 'outlook.com', 'outlook.be', 'live.com', 'live.be', 'msn.com',
            'yahoo.com', 'yahoo.fr', 'icloud.com', 'me.com', 'mac.com', 'proton.me', 'protonmail.com', 'telenet.be', 'skynet.be',
            'gmx.com', 'gmx.de', 'web.de', 'aol.com', 'pandora.be', 'scarlet.be', 'proximus.be', 'yandex.com', 'zoho.com'}
ROLE = re.compile(r'^(?:info|support|hello|hi|team|sales|billing|admin|office|contact|help|accounts?|finance|jobs|hr|press|legal|security|privacy|feedback)@')
GENERIC_NAMES = {'you', 'linkedin member', 'x conversation', 'linkedin conversation', 'unknown', 'unknown sender', 'unknown caller'}
WEIGHTS = {'owner': 0.6, 'them': 0.3, 'met': 0.5}
DECISION_WEIGHT = {'accept': 1.0, 'send': 1.0, 'complete': 1.0, 'other': 0.5, 'reject': -0.5}
# Cosine between local embeddings (see embeddings.py). Meaning alone groups by genre (every partnership
# email reads alike), so a probable link also needs a shared person; tried on live data, 2026-10-18.
PROBABLE = 0.72
UNNAMED = 0.75
UNNAMED_DAYS = 45
UNNAMED_KINDS = {'gmail.message', 'calendar.event', 'salesflare.email', 'salesflare.meeting', 'intercom.conversation', 'github.notification'}
OPEN_CARD = ('pending', 'accepted', 'prepared', 'instructions', 'snoozed')
# Boards: projects of one kind (partner listings, buyers, onboardings) in stages. Every board ends in this row too.
STALLED = 'Stalled or declined'
PROJECT_STAGE_COLUMNS = ('board_id INTEGER', 'stage TEXT', 'stage_reason TEXT', 'stage_event_id INTEGER', 'stage_by TEXT', 'stage_at TEXT',
                         'next_step TEXT', 'org TEXT')
BOARD_COLUMNS = ('about TEXT', 'side INTEGER NOT NULL DEFAULT 1', 'updated_at TEXT', 'visibility TEXT')
# Who sees a board and its projects (2026-10-24): 'only_you' (never on the Team Graph, also not its timelines) or 'team'.
# Text, so named people can be added later. A project's own visibility (NULL: its board's) overrides it.
VISIBILITY = ('only_you', 'team')
MAX_OPEN_SUGGESTIONS = 3  # board suggestions open at once
# Sorting a project the owner put on a board (2026-10-24): the review run it was last given to, how many reviews got it,
# and why a review couldn't place it (shown on its tile)
SORT_COLUMNS = ('sort_run TEXT', 'sort_tries INTEGER NOT NULL DEFAULT 0', 'sort_note TEXT')
SORT_TRIES = 3          # reviews that get an unplaced project before its tile says sorting failed
SORT_BATCH = 12         # projects to sort per review
SORT_MESSAGES = 6       # each one's latest messages shown to the review
SORT_RESET = 'sort_run=NULL,sort_tries=0,sort_note=NULL'
# What the owner keeps on a project like in a CRM (2026-10-24): a value, notes, who made it (the owner's own stays without
# messages), and how it ended (won or lost, with why)
PROJECT_CRM_COLUMNS = ('value REAL', 'notes TEXT', 'made_by TEXT', 'outcome TEXT', 'outcome_reason TEXT', 'close_date TEXT')
BOARD_EXTRA_COLUMNS = ('is_default INTEGER NOT NULL DEFAULT 0',)
FIT_BATCH = 40          # projects on no board a review checks against the boards at a time
FIT_FRESH_MINUTES = 30  # a board saved this recently starts a review for its check at once
MAX_STAGES = 8
# A new board looks through the whole history once (2026-10-24): every organization (or person without one) with
# messages or meetings in no project yet is checked against it, BACKFILL_BATCH at a time in each review, the likeliest
# first (by meaning, when there are local embeddings), at most BACKFILL_MAX per board.
BACKFILL_BATCH = 30
BACKFILL_MAX = 400
BACKFILL_CHARS = 14000  # the block's size in one review
BACKFILL_SHOWN_DAYS = 3  # the board says what it found for this long
BACKFILL_GIVE_UP_DAYS = 7
BACKFILL_KINDS = ('gmail.message', 'salesflare.email', 'linkedin.message', 'slack.message', 'whatsapp.message', 'intercom.conversation', 'x.dm',
                  'ms_graph.message', 'outlook.message', 'calendar.event', 'salesflare.meeting', 'outlook.event', 'ms_graph.event')
STUCK_DAYS = 10         # no reply for this long after the owner wrote: the tile says so


def ensure(db):
    """Create the graph tables, and add columns that came later."""
    db.executescript(SCHEMA)
    if 'answer' not in {r[1] for r in db.execute('PRAGMA table_info(board_offers)')}:
        db.execute('ALTER TABLE board_offers ADD COLUMN answer TEXT')
    if 'probable' not in {r[1] for r in db.execute('PRAGMA table_info(graph_events)')}:
        db.execute('ALTER TABLE graph_events ADD COLUMN probable INTEGER NOT NULL DEFAULT 0')
    if 'direct' not in {r[1] for r in db.execute('PRAGMA table_info(graph_events)')}:
        db.execute('ALTER TABLE graph_events ADD COLUMN direct INTEGER NOT NULL DEFAULT 0')
    if 'source' not in {r[1] for r in db.execute('PRAGMA table_info(graph_merges)')}:  # who said it (2026-10-23): the owner, or a review (older rows)
        db.execute('ALTER TABLE graph_merges ADD COLUMN source TEXT')
    have = {r[1] for r in db.execute('PRAGMA table_info(person_roles)')}
    for column in ('event_id INTEGER', 'quote TEXT', 'hints TEXT', 'checked_at TEXT'):  # a role's evidence and when it was last read
        if column.split()[0] not in have:
            db.execute('ALTER TABLE person_roles ADD COLUMN ' + column)
    if 'people_shown' not in {r[1] for r in db.execute('PRAGMA table_info(graph_role_asks)')}:  # the ask showed an organization's people with their roles
        db.execute('ALTER TABLE graph_role_asks ADD COLUMN people_shown INTEGER NOT NULL DEFAULT 0')
    have = {r[1] for r in db.execute('PRAGMA table_info(projects)')}
    for column in PROJECT_STAGE_COLUMNS + SORT_COLUMNS + PROJECT_CRM_COLUMNS:  # a project's place on a board (2026-10-23): its stage, why it last moved and from which message
        if column.split()[0] not in have:
            db.execute('ALTER TABLE projects ADD COLUMN ' + column)
    if 'split' not in {r[1] for r in db.execute('PRAGMA table_info(board_fits)')}:  # a review said the project bundles several counterparts (2026-10-24)
        db.execute('ALTER TABLE board_fits ADD COLUMN split INTEGER')
    have = {r[1] for r in db.execute('PRAGMA table_info(boards)')}
    for column in BOARD_COLUMNS + BOARD_EXTRA_COLUMNS:  # the owner's own boards (2026-10-24): which projects belong, and whether it has a Stalled row
        if column.split()[0] not in have:
            db.execute('ALTER TABLE boards ADD COLUMN ' + column)
    if not db.execute("SELECT 1 FROM board_backfills LIMIT 1").fetchone() and not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='board_backfills_queued'").fetchone():
        # Boards made before boards looked through the history get that look once (2026-10-24)
        db.execute('CREATE TABLE IF NOT EXISTS board_backfills_queued(at TEXT)')
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        db.execute('INSERT INTO board_backfills_queued VALUES(?)', (stamp,))
        if 'about' in {r[1] for r in db.execute('PRAGMA table_info(boards)')}:
            db.execute('INSERT OR IGNORE INTO board_backfills(board_id,started_at) SELECT id,? FROM boards', (stamp,))
    for table in ('projects', 'board_suggestions'):  # who sees it (2026-10-24): a project's own (NULL: its board's), a suggestion's default
        if 'visibility' not in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}:
            db.execute(f'ALTER TABLE {table} ADD COLUMN visibility TEXT')


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


# System and relay addresses: never a person, also not when the owner wrote to one (a reply through Intercom goes to a
# relay). Intercom's Operator bot and its reply relays, GitHub's noreply and reply addresses, reply+token relays of
# feedback and ticket tools, and the notification senders of Slack, Google Calendar and GitHub.
RELAY = re.compile(r'^operator\+[^@]*@intercom\.io$|@(?:[\w-]+\.)*intercom-mail\.(?:com|eu|io)$'
                   r'|@(?:[\w-]+\.)*(?:no-?reply|reply)\.github\.com$|@(?:[\w-]+\.)*no-?reply\.[\w-]+(?:\.[\w-]+)+$'
                   r'|^(?:reply|replies)[+=][^@]+@'
                   r'|(?:^|[._+-])(?:no[._-]?reply|do[._-]?not[._-]?reply|notifications?)(?:[._+-]|@)'
                   r'|^(?:calendar-notification|calendar-server|notify|feedback|bot)@(?:[\w-]+\.)*(?:google|slack|github)\.com$', re.I)


def relay(address):
    """A system or relay address: never a person, whoever wrote to it."""
    return bool(RELAY.search(str(address or '').lower()))


DOMAIN_NAME = re.compile(r'[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}')  # "HelloTV.nl": a sender's domain, not a person's name
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
    if not name or '@' in name or name.casefold() in GENERIC_NAMES or DOMAIN_NAME.fullmatch(name):
        return None
    folded = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().casefold()
    words = re.findall(r'[a-z]+', folded)
    return ' '.join(words) if len(words) >= 2 and all(len(w) > 1 for w in words[:2]) else None


def touches(kind, m, owner, wrote_to):
    """[(identifier, name, weight, role)] for one signal. Roles: 'owner' (the owner wrote to them),
    'them' (they wrote to the owner directly), 'met' (a meeting with the owner), 'seen' (present).
    A system or relay address is never one of them."""
    return [t for t in _touches(kind, relayed(kind, m), owner, wrote_to) if not (t[0].startswith('email:') and relay(t[0][6:]))]


def relayed(kind, m):
    """The signal as its real sender wrote it: an Intercom conversation its Operator bot opened names the customer."""
    if kind == 'intercom.conversation' and m.get('contact_email') and relay(next((a for _, a in addresses(m.get('from'))), '')):
        return {**m, 'from': m['contact_email'], 'from_name': m.get('contact_name') or ''}
    return m


def _touches(kind, m, owner, wrote_to):
    if kind == 'gmail.message':
        labels = set(m.get('labels') or ())
        if labels & {'SPAM', 'TRASH'}:
            return []
        sender = addresses(m.get('from'))[:1]
        to, cc = addresses(m.get('to')), addresses(m.get('cc'))
        if 'SENT' in labels or (sender and sender[0][1] in owner.emails):
            to = [(n, a) for n, a in to if not owner.mine(a) and not relay(a)]
            cc = [(n, a) for n, a in cc if not owner.mine(a) and not relay(a)]
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


# Same person? Why two identities were joined (strong evidence), as the person page says it.
JOINED_WHY = {'owner': 'merged by you', 'agent': 'merged by a review', 'directory': 'their Slack profile has this address',
              'message': 'same name, both on one message', 'thread': 'same name, both in one conversation',
              'profile': 'the same LinkedIn profile', 'signature': 'this number is in their own email', 'name': 'same full name on two channels'}
PHONE = re.compile(r'(?<![\w+])(?:\+|00)?\d[\d ()./-]{7,18}\d(?![\w])')
QUOTED = re.compile(r'^(?:>|On .{4,200} wrote:\s*$|-{2,} ?(?:Original|Forwarded) |From: |Van: |Le .{4,200} a écrit|Am .{4,200} schrieb)', re.M)
SIGNATURE_MAILS = 12     # recent mails read per address for a phone number in its own text
SAME_BUCKET = 60         # more people than this with one first name: too common to compare


def name_words(name, identifier=None):
    """Folded words of a person's name; for an address with no name, of its local part when that reads as a full name
    (jane.doe@ → jane doe). Empty when there is no name."""
    name = person_name(name) if name else ''
    if name and '@' not in name and not DOMAIN_NAME.fullmatch(name) and name.casefold() not in GENERIC_NAMES and name != shown_id(identifier or '', {}):
        folded = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().casefold()
        return re.findall(r'[a-z]+', folded)
    if identifier and identifier.startswith('email:'):
        local = identifier[6:].split('@', 1)[0]
        parts = re.split(r'[._-]', local.casefold())
        if len(parts) >= 2 and all(re.fullmatch(r'[a-z]{2,}', p) for p in parts) and not ROLE.match(local + '@'):
            return parts
    return []


def name_match(a, b):
    """'same' (the same full name), 'similar' (the same first name, or one a short form of the other, and no other
    surname: "Ann" and "Ann Lee", "Alex Lee" and "Alexander Lee", "Ann Lee" and "Ann Marie Lee") or None."""
    if not a or not b:
        return None
    if a == b:
        return 'same' if len(a) >= 2 else 'similar'
    first = a[0] == b[0] or (min(len(a[0]), len(b[0])) >= 3 and (a[0].startswith(b[0]) or b[0].startswith(a[0])))
    if not first:
        return None
    if len(a) >= 2 and len(b) >= 2 and a[-1] != b[-1]:
        return None  # another surname: another person
    return 'similar'


def linkedin_key(url):
    found = re.search(r'linkedin\.com/in/([^/?#\s]+)', str(url or ''), re.I)
    return found[1].casefold() if found else None


def own_text(body):
    """A message's own text: without the quoted mail it answers or forwards, where other people's signatures are."""
    body = str(body or '')
    cut = QUOTED.search(body)
    return body[:cut.start()] if cut else body


def signature_pairs(db, senders, best, known):
    """(email identifier, WhatsApp identifier) pairs where a WhatsApp number is in mail that address wrote itself (its
    signature, "call me on ...") and both carry a matching name: a number alone could be a colleague's or an office's."""
    phones = {}
    for identifier in known:
        if identifier.startswith('wa:') and identifier[3:].isdigit() and len(identifier) >= 12:
            phones.setdefault(identifier[-9:], []).append(identifier)
    wa_names = {i: name_words(best(i), i) for ids in phones.values() for i in ids}
    wanted = {}
    for identifier, mails in senders.items():
        words = name_words(best(identifier), identifier)
        if words and any(name_match(words, w) for w in wa_names.values()):
            wanted[identifier] = [e for _, e in sorted(mails, reverse=True)[:SIGNATURE_MAILS]]
    if not wanted:
        return []
    event_ids = sorted({e for ids in wanted.values() for e in ids})
    bodies = {}
    for start in range(0, len(event_ids), 500):
        chunk = event_ids[start:start + 500]
        bodies.update({r[0]: r[1] for r in db.execute(f"SELECT id,body FROM events WHERE id IN ({','.join('?' * len(chunk))})", chunk)})
    pairs = []
    for identifier, ids in sorted(wanted.items()):
        words = name_words(best(identifier), identifier)
        numbers = {re.sub(r'\D', '', p)[-9:] for e in ids for p in PHONE.findall(own_text(bodies.get(e))) if len(re.sub(r'\D', '', p)) >= 9}
        for number in sorted(numbers):
            for wa in phones.get(number, ()):
                if name_match(words, wa_names[wa]):
                    pairs.append((identifier, wa))
    return pairs


def likely_same(people, union, together, projects):
    """Pairs of people who may be one, for the owner to Merge or keep apart: [(a, b, why, score)]. The same full name
    is enough to ask (it was not joined because two people on one channel carry it); a similar name needs something
    shared besides: the same company, people seen with both, a project, or the same role."""
    buckets = collections.defaultdict(list)
    for node, p in people.items():
        if p['words']:
            buckets[p['words'][0]].append(node)
            if len(p['words'][0]) >= 3:
                buckets[p['words'][0][:3]].append(node)  # "Alex" and "Alexander"
    seen, out = set(), []
    for members in buckets.values():
        if len(members) > SAME_BUCKET:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                pair = tuple(sorted((a, b)))
                if pair in seen or a == b:
                    continue
                seen.add(pair)
                pa, pb = people[pair[0]], people[pair[1]]
                match = name_match(pa['words'], pb['words'])
                if not match or union.kept_apart(union.find(pair[0]), union.find(pair[1])):
                    continue
                shared = []
                if pa['domains'] & pb['domains']:
                    shared.append('both at ' + sorted(pa['domains'] & pb['domains'])[0])
                if pa['role'] and pa['role'] == pb['role']:
                    shared.append('the same role')
                if together(pair[0]) & together(pair[1]):
                    shared.append('with the same people')
                if projects.get(pair[0], set()) & projects.get(pair[1], set()):
                    shared.append('on the same project')
                if match == 'same' and len(pa['words']) >= 2 or shared:
                    why = ', '.join([('Same name' if match == 'same' else 'Similar name')] + shared)
                    out.append((*pair, why, round(len(shared) + (2 if match == 'same' else 0) + min(pa['strength'], pb['strength']), 4)))
    return sorted(out, key=lambda s: -s[3])[:300]


class Union:
    """Identifiers joined into people; a pair said to be different is never joined, directly or through others.
    Each join is kept as (a, b, why), so a person's page can say why they are one and undo it."""
    def __init__(self, apart=()):
        self.parent, self.members, self.apart, self.edges = {}, {}, set(apart), []

    def find(self, x):
        if x not in self.parent:
            self.parent[x], self.members[x] = x, {x}
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b, why=None):
        first, second = a, b
        a, b = self.find(a), self.find(b)
        if a == b or self.kept_apart(a, b):
            return False
        # An email address names the node when there is one: stable and recognizable.
        if b.startswith('email:') and not a.startswith('email:'):
            a, b = b, a
        self.parent[b] = a
        self.members[a] |= self.members.pop(b)
        self.edges.append((first, second, why))
        return True

    def kept_apart(self, a, b):
        """True when someone said a member of one is not the same as a member of the other."""
        return bool(self.apart) and any(frozenset((x, y)) in self.apart for x in self.members[a] for y in self.members[b])


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
    """Signals that read like a labeled one (cosine >= PROBABLE) and share a person with it:
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
    gone = forgotten(db)  # people the owner deleted permanently: never added again

    # Who: identifiers per signal, joined into people.
    seen_message_ids = set()
    per_event, names, x_names = {}, collections.defaultdict(collections.Counter), {}
    crm_names, paying_names = {}, {}
    appears = collections.defaultdict(set)  # identifier -> where they show up: a hint for a review, never a role
    owner_events = set()
    headlines = set()  # LinkedIn conversations whose person has a headline (read at view time: it stays sealed in the signal)
    named_on = collections.defaultdict(lambda: collections.defaultdict(set))  # (message or conversation) -> full name -> identifiers
    senders = collections.defaultdict(list)  # email identifier -> [(at, event id)] of mail it wrote: its own text may carry its number
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
            for _, addr in addresses(relayed(kind, m).get('from'))[:1]:
                if addr.rsplit('@', 1)[1] in owner.domains or relay(addr):
                    continue  # a teammate answering support or sending an invoice
                appears['email:' + addr].add(('billed through Stripe' + (' as ' + str(m['customer_name']) if m.get('customer_name') else ''))
                                             if kind == 'stripe.event' else 'wrote to support (Intercom)')
                domain = addr.rsplit('@', 1)[1]
                if kind == 'stripe.event' and m.get('customer_name') and domain not in FREEMAIL:
                    paying_names.setdefault(domain, str(m['customer_name']))
        found = [t for t in touches(kind, m, owner, wrote_to) if not (t[0].startswith('email:') and t[0].rsplit('@', 1)[1] in warmup) and not gone(t[0])]
        if not found:
            continue
        if kind == 'linkedin.message' and isinstance(m.get('headline'), str) and m['headline'].strip() and not m.get('group'):
            headlines.add('li:' + str(m['conversation_id']))
        per_event[event_id] = (kind, at, account_id, m, found)
        key, exact = thread_key(kind, m)
        for identifier, name, _, _ in found:
            full = plain_name(name) if name and '@' not in str(name) else None
            if full and full not in owner.names and not ROLE.match(identifier.split(':', 1)[-1]):
                named_on[('message', event_id)][full].add(identifier)
                if key and exact:
                    named_on[('thread', account_id, key)][full].add(identifier)
        if kind == 'gmail.message' and found[0][3] in ('them', 'seen') and found[0][0].startswith('email:'):
            senders[found[0][0]].append((at, event_id))
        if any(role in ('owner', 'met') for _, _, _, role in found):
            owner_events.add(event_id)
        for identifier, name, _, _ in found:
            if name and str(name).strip() and '@' not in str(name) and str(name).strip().casefold() not in GENERIC_NAMES:
                names[identifier][person_name(name)] += 1
            if identifier.startswith('x:') and m.get('screen_name') and not m.get('from_me'):
                x_names[identifier] = m['screen_name']
    merges = db.execute('SELECT a,b,same,source FROM graph_merges ORDER BY created_at').fetchall()
    union = Union(frozenset((r['a'], r['b'])) for r in merges if not r['same'])  # Not the same, and an undone merge
    known = {identifier for (_, _, _, _, found) in per_event.values() for identifier, _, _, _ in found}
    for identifier in sorted(known):
        union.find(identifier)
    for r in merges:
        if r['same'] and not gone(r['a']) and not gone(r['b']):
            union.join(r['a'], r['b'], 'owner' if r['source'] == 'owner' else 'agent')
    for r in db.execute('SELECT identifier,name,email FROM identity_names'):
        if gone(r['identifier']) or (r['email'] and gone('email:' + r['email'].lower())):
            continue
        if r['name']:
            names[r['identifier']][person_name(r['name'])] += 3
        if r['email'] and r['identifier'] in known:
            union.join('email:' + r['email'].lower(), r['identifier'], 'directory')

    # People the owner added by hand (an address, or none yet): in the graph from the start, as known identities.
    manual = db.execute('SELECT identifier,kind,name FROM manual_nodes').fetchall()
    manual_people = [r['identifier'] for r in manual if r['kind'] == 'person' and not gone(r['identifier'])]
    for r in manual:
        if r['kind'] == 'person' and not gone(r['identifier']):
            known.add(r['identifier'])
            union.find(r['identifier'])
            names[r['identifier']][person_name(r['name']) or r['name']] += 5

    def best(identifier):
        top = names[identifier].most_common(1)
        return top[0][0] if top else None
    # Strong evidence, never a name alone: the same full name on two identities in one message (someone writing from
    # work with their own address in CC) or one conversation, ...
    for where, by_full in sorted(named_on.items(), key=lambda item: item[0][0] != 'message'):
        for full, group in sorted(by_full.items()):
            group = sorted(group, key=lambda i: (not i.startswith('email:'), i))
            for other in group[1:]:
                union.join(group[0], other, where[0])
    # ... the same LinkedIn profile, found for both by a lookup, and their WhatsApp number in mail they wrote themselves.
    by_profile = collections.defaultdict(set)
    for r in db.execute("SELECT identifier,profile FROM person_profiles WHERE status='found' AND profile IS NOT NULL"):
        try:
            url = linkedin_key(json.loads(r['profile']).get('linkedin_url')) if r['identifier'] in known else None
        except (ValueError, AttributeError):
            url = None
        if url:
            by_profile[url].add(r['identifier'])
    for url, group in sorted(by_profile.items()):
        group = sorted(group, key=lambda i: (not i.startswith('email:'), i))
        for other in group[1:]:
            union.join(group[0], other, 'profile')
    for address, number in signature_pairs(db, senders, best, known):
        union.join(address, number, 'signature')
    by_name = collections.defaultdict(set)
    for identifier in known:
        full = plain_name(best(identifier))
        if full:
            by_name[full].add(identifier)
    for full, group in sorted(by_name.items()):
        if full in owner.names:
            continue  # the owner, writing from another channel
        spaces = collections.defaultdict(set)
        for i in group:
            spaces[i.split(':', 1)[0]].add(union.find(i))
        # The same full name on different channels is one person, unless two people carry it on one channel
        # (two addresses or two chats not shown to be one) or someone said they differ: then it is only a question.
        if len(spaces) > 1 and all(len(roots) == 1 for roots in spaces.values()):
            group = sorted(group, key=lambda i: (not i.startswith('email:'), i))
            for other in group[1:]:
                union.join(group[0], other, 'name')
    joined = collections.defaultdict(list)
    for a, b, why in union.edges:
        joined[union.find(a)].append((a, b, why))

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
    projects = {r['id']: dict(r) for r in db.execute('SELECT id,name,created_at,closed_at,board_id,stage,made_by FROM projects')}
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
    for r in db.execute('SELECT project_id,node_id FROM project_people'):  # people the owner put on a project themselves
        if r['project_id'] in projects:
            key = (r['node_id'], r['project_id'])
            project_people[key] = max(project_people[key], 0.5)
            project_people_last.setdefault(key, current)
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
    for identifier in manual_people:  # added by hand: shown even before the first message
        node = union.find(identifier)
        strength[node] = max(strength[node], FLOOR)

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
    looked_up = {r['identifier']: r['profile'] for r in db.execute("SELECT identifier,profile FROM person_profiles WHERE status='found' AND profile IS NOT NULL")}
    photos = {r['identifier']: r['key'] for r in db.execute('SELECT identifier,key FROM person_photos')}
    facts = enrichment.people_facts(db)  # what connectors read about them (job title, place, links, time zone)
    edits = {r['domain']: json.loads(r['fields']) for r in db.execute('SELECT domain,fields FROM org_edits')}
    person_edits = {r['identifier']: json.loads(r['fields']) for r in db.execute('SELECT identifier,fields FROM person_edits ORDER BY updated_at,identifier')}
    stated_rows = {r['identifier']: dict(r) for r in db.execute('SELECT * FROM person_orgs')}
    stated = {}  # person node -> the statement of where they work, when no work address says it
    named_orgs = collections.defaultdict(list)
    surnames = {n.split()[-1] for n in owner.names if len(n.split()) >= 2}
    alike = {}  # person node -> what "Same person?" compares: name words, company domains, role
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
        person_profile = next((json.loads(looked_up[i]) for i in group if i in looked_up), None)
        # Only a photo saved on this Machine shows (served by the dashboard), never the service's own link.
        person_profile = {k: v for k, v in (person_profile or {}).items() if k not in ('photo', 'photo_gone')}
        person_profile = enrichment.with_facts(person_profile, [facts[i] for i in group if i in facts])
        photo_key = next((photos[i] for i in group if i in photos), None)
        if photo_key:
            person_profile['photo'] = 'api/person-photo?p=' + photo_key
        edited = {}
        for identifier in sorted((i for i in group if i in person_edits), key=list(person_edits).index):
            edited.update(person_edits[identifier])  # the owner's word wins over the look-up, a later save over an older one
        mine = {k: edited[k] for k in ('title', 'location', 'linkedin_url') if edited.get(k)}
        if mine:
            person_profile.update(mine)
            person_profile['sources'] = {**(person_profile.get('sources') or {}), **{k: {'from': 'you', 'own': True} for k in mine}}
        contacts = shown_contacts(group, edited)
        stated_phone = ' '.join(str(person_profile.get('phone') or '').split())  # a number a connector read (their Slack profile)
        gone = {contact_key('phones', v) for v in (edited.get('phones') or {}).get('removed', ())}
        if PHONE.fullmatch(stated_phone) and contact_key('phones', stated_phone) not in gone | {contact_key('phones', r['value']) for r in contacts['phones']}:
            contacts['phones'].append({'value': stated_phone, 'by': ((person_profile.get('sources') or {}).get('phone') or {}).get('from') or 'messages'})
        person_profile = person_profile or None
        side = edited.get('side') or ('work' if colleague or domains or (said_org and said_org['org']) else 'personal')
        info = {'ids': sorted({shown_id(i, x_names) for i in group if not i.startswith('manual:')}), 'org': ('org:' + domains[0]) if domains and not said_org else None,
                'org_by': 'email' if domains and not said_org else None, 'has_headline': any(i in headlines for i in group),
                'wrote': len(roles[node]['owner']), 'heard': len(roles[node]['them']), 'met': len(roles[node]['met']),
                'accounts': sorted(accounts[node]), 'next_meeting': next_meeting[node].isoformat() if node in next_meeting else None,
                'colleague': colleague, 'role': role, 'role_by': role_by, 'role_id': role_id, 'identifiers': group, 'hints': hints, 'seen_only': not talk,
                'profile': person_profile, 'edited': sorted(edited), 'side': side, 'side_by': 'owner' if edited.get('side') else None,
                'manual': any(i in manual_people for i in group), 'notes': edited.get('notes'),
                **contacts}
        if len(group) > 1:
            info['joined'] = [{'a': x, 'b': y, 'a_shown': shown_id(x, x_names), 'b_shown': shown_id(y, x_names), 'why': JOINED_WHY.get(why, 'joined'),
                               'by': why} for x, y, why in joined.get(node, ()) if why]
        alike[node] = {'words': name_words(name, node if name == shown_id(node, x_names) else None), 'strength': value,
                       'domains': set(domains), 'role': ' '.join(str(role).casefold().split()) if role else None}
        nodes.append((node, 'person', name[:200], round(value, 4), last_day[node].isoformat() if node in last_day else None,
                      len(node_events[node]), usual, int(quiet), None, info))
    # Organizations the owner added by hand stand even with nobody in them yet.
    for r in manual:
        if r['kind'] == 'org' and r['identifier'].startswith('org:named:'):
            named_orgs.setdefault(r['identifier'], [])
        elif r['kind'] == 'org' and r['identifier'][4:] not in owner.domains | warmup:
            members.setdefault(r['identifier'][4:], [])

    # Stated organizations join the one with that name or domain, or stand as their own (no domain, never looked up).
    def org_name(domain):
        # A CRM or Stripe name that is only the domain says nothing; a looked-up or readable one reads better.
        said = [n for n in (crm_names.get(domain), paying_names.get(domain)) if n and str(n).strip().lower().removeprefix('www.') != domain]
        return (named.get('org:' + domain) or (said[0] if said else None)
                or ({**(profiles.get(domain) or {}), **edits.get(domain, {})}).get('name') or enrichment.readable_name(domain))
    by_org_name = {}
    for domain in sorted(members):
        by_org_name.setdefault(folded_words(org_name(domain)), domain)
    for domain in sorted(members):  # "Acme" is acme.com when no organization is called that
        by_org_name.setdefault(folded_words(domain.split('.')[0]), domain)
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
    # Same person? Likely pairs for the owner to Merge or keep apart, on both pages and in People.
    seen_with = {}

    def together(node):
        if node not in seen_with:
            seen_with[node] = {p for _, e in node_events[node] for p in event_people.get(e, ())} - {node}
        return seen_with[node]
    on_projects = collections.defaultdict(set)
    for (node, pid), value in project_people.items():
        if value >= FLOOR:
            on_projects[node].add(pid)
    same_rows = likely_same(alike, union, together, on_projects)
    for a, b, why, _ in same_rows:
        for one, other in ((a, b), (b, a)):
            asks = infos[one].setdefault('same', [])
            if len(asks) < 3:
                asks.append({'id': other, 'why': why})
    names_now = {n[0]: n[2] for n in nodes}
    for info in infos.values():
        for ask in info.get('same', ()):
            ask['name'] = names_now.get(ask['id'])
    nodes = [n[:9] + (json.dumps(n[9]),) if isinstance(n[9], dict) else n for n in nodes]
    kept = {n[0] for n in nodes}
    for org_id, people in named_orgs.items():
        role, role_by = said.get(org_id, (None, None))
        made = next((r['name'] for r in manual if r['identifier'] == org_id), None)
        nodes.append((org_id, 'org', (named.get(org_id) or (collections.Counter(p[2] for p in people).most_common(1)[0][0] if people else made) or org_id)[:200],
                      round(sum(v for _, v, _ in people), 4), max((last_day[p].isoformat() for p, _, _ in people if p in last_day), default=None),
                      len(people), None, 0, None, json.dumps({'domain': None, 'role': role, 'role_by': role_by, 'paying': False, 'profile': edits.get(org_id) or None,
                                                              'edited': sorted(edits.get(org_id, {})), 'wrong': False, 'stated': True,
                                                              'manual': made is not None})))
        for node, value, _ in people:
            links.append((node, org_id, 'member', round(value, 4), None))
    for domain, people in members.items():
        total = sum(v for _, v in people)
        role, role_by = said.get('org:' + domain, (None, None))
        # What the owner wrote wins; the email domain is the website when nothing else says one.
        profile = {'website': enrichment.website(domain), **(profiles.get(domain) or {}), **edits.get(domain, {})}
        nodes.append(('org:' + domain, 'org', org_name(domain)[:200], round(total, 4),
                      max((last_day[p].isoformat() for p, _ in people if p in last_day), default=None), len(people), None, 0, None,
                      json.dumps({'domain': domain, 'role': role, 'role_by': role_by, 'paying': domain in paying_names, 'profile': profile,
                                  'edited': sorted(edits.get(domain, {})), 'wrong': domain in wrong,
                                  'manual': any(r['identifier'] == 'org:' + domain for r in manual)})))
        for node, value in people:
            links.append((node, 'org:' + domain, 'member', round(value, 4), None))
    two_weeks = current - dt.timedelta(days=14)
    for pid, project in projects.items():
        value, before = heat(pid, current), heat(pid, two_weeks)
        if value < FLOOR and not project_events.get(pid) and not project['board_id'] and not project['made_by']:  # on a board or made by the owner it stays
            continue
        events_ = sorted(project_events.get(pid, ()))
        # Last signal is what already happened; a meeting still to come is the next one, never the last.
        past = [e for e in events_ if e[0] <= current]
        coming = [e for e in events_ if e[0] > current]
        nodes.append(('project:' + str(pid), 'project', project['name'], round(value, 4), past[-1][0].astimezone(tz).date().isoformat() if past else None,
                      len(past), None, 0, round(value / before, 2) if before > FLOOR else None,
                      json.dumps({'closed_at': project['closed_at'], 'created_at': project['created_at'], 'probable': sum(1 for e in past if e[2]),
                                  'board_id': project['board_id'], 'stage': project['stage'],
                                  'next_meeting': coming[0][0].isoformat() if coming else None})))
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
        db.execute('DELETE FROM graph_same')
        db.executemany('INSERT OR REPLACE INTO graph_same(a,b,why,score) VALUES(?,?,?,?)', same_rows)
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
            'links': len(links), 'signals': len(signals), 'probable': len(probable), 'unnamed': len(unnamed_rows), 'same': len(same_rows),
            'joined': sum(1 for _, _, why in union.edges if why in ('message', 'thread', 'profile', 'signature', 'name')), 'took_ms': took}


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
                    org_via=info.get('org_via'), org_event_id=info.get('org_event_id'), org_at=info.get('org_at'),
                    profile=info.get('profile'), joined=info.get('joined', []), same=info.get('same', []), edited=info.get('edited', []),
                    side=info.get('side'), side_by=info.get('side_by'), phones=info.get('phones', []), emails=info.get('emails', []),
                    manual=bool(info.get('manual')), notes=info.get('notes'))
    elif row['kind'] == 'org':
        view.update(role=info.get('role'), role_by=info.get('role_by'), domain=info.get('domain'), profile=info.get('profile'),
                    about=enrichment.summary(info.get('profile')), edited=info.get('edited', []), wrong=bool(info.get('wrong')),
                    manual=bool(info.get('manual')))
    elif row['kind'] == 'project':
        view.update(trend=row['trend'], state=project_state(row), closed=bool(info.get('closed_at')), probable_signals=info.get('probable', 0),
                    next_meeting=info.get('next_meeting'))
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
            title = (view.get('profile') or {}).get('title')
            bits = ([view['role']] if view['role'] else ['no role yet']) + ([f'job title {title}'] if title else []) \
                + ([f"at {org['name']}" + (f" ({'; '.join(org_bits)})" if org_bits else '')] if org else []) \
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
            lines.append(f"- Project {view['name']}: {view['state']}, last signal {short_date(view['last_at'])}"
                         + (f", next meeting {short_date(view['next_meeting'])}" if view.get('next_meeting') else '') + (f"; people: {', '.join(people)}" if people else '')
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
            'warmup': hidden_list(db),
            'same_person': same_people(db),
            'counts': counts(db),
            'list_counts': list_counts(db)}


def counts(db):
    """How many people and organizations the graph knows, and how many projects are open: the sidebar's Graph rows."""
    found = dict(db.execute("SELECT kind,COUNT(*) FROM graph_nodes WHERE kind IN ('person','org') GROUP BY kind").fetchall())
    found['project'] = db.execute("SELECT COUNT(*) FROM graph_nodes WHERE kind='project' AND json_extract(info,'$.closed_at') IS NULL").fetchone()[0]
    return {kind: found.get(kind, 0) for kind in ('person', 'org', 'project')}


def _week_ago(db):
    state = db.execute('SELECT built_at FROM graph_state WHERE id=1').fetchone()
    return ((parse_time(state['built_at']) if state else dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=7)).date().isoformat()


# The lists the Graph panes show as tabs, each the whole list, strongest first: People (Closest, Gone quiet, New this
# week), Organizations (Closest, Warm up) and Projects (Open, Done). The overview keeps its short lists for the agent.
PAGED = {'people': ("kind='person'", ()), 'quiet': ("kind='person' AND quiet=1", ()),
         'new': ("kind='person' AND signals<=3 AND last_at>=?", (_week_ago,)),
         'work': ("kind='person' AND json_extract(info,'$.side')='work'", ()), 'personal': ("kind='person' AND json_extract(info,'$.side')='personal'", ()),
         'orgs': ("kind='org'", ()),
         'projects': ("kind='project' AND json_extract(info,'$.closed_at') IS NULL", ()),
         'projects_done': ("kind='project' AND json_extract(info,'$.closed_at') IS NOT NULL", ())}


def _paged_where(db, name):
    where, args = PAGED[name]
    return where, tuple(a(db) for a in args)


STAGE = "CASE WHEN strength<0.3 THEN 3 WHEN trend IS NOT NULL AND trend<0.8 THEN 2 WHEN trend IS NULL OR trend>1.3 THEN 0 ELSE 1 END"
SORTS = {'closest': 'strength DESC', 'last': 'last_at DESC', 'name': 'name COLLATE NOCASE', 'stage': STAGE + ',strength DESC'}
DEFAULT_SORT = {'projects': 'last', 'projects_done': 'last'}


def page(db, name, sort='', cursor=0, limit=30):
    """One page of a Graph tab's list in the chosen order (closest, last contact, name, or a project's stage); the
    cursor is where the page starts, next the cursor of the page after it, or None at the end."""
    if name not in PAGED and name != 'warmup':
        raise ValueError('list is one of ' + ', '.join([*PAGED, 'warmup']))
    sort = sort if sort in SORTS and (sort != 'stage' or name.startswith('projects')) else DEFAULT_SORT.get(name, 'closest')
    offset, limit = max(0, int(cursor or 0)), max(1, min(int(limit), 50))
    if name == 'warmup':
        order = 'coalesce(name,domain) COLLATE NOCASE,domain' if sort == 'name' else 'created_at DESC,domain'
        rows = [dict(r) for r in db.execute(f'SELECT domain,name,source,created_at FROM hidden_domains WHERE hidden=1 ORDER BY {order} LIMIT ? OFFSET ?',
                                            (limit + 1, offset))]
        items = rows[:limit]
    else:
        where, args = _paged_where(db, name)
        rows = db.execute(f'SELECT * FROM graph_nodes WHERE {where} ORDER BY {SORTS[sort]},id LIMIT ? OFFSET ?', (*args, limit + 1, offset)).fetchall()
        items = [node_view(r) for r in rows[:limit]]
        orgs = {v.get('org') for v in items if v.get('kind') == 'person' and v.get('org')}
        if orgs:  # the organization's name, not only its id
            named = dict(db.execute(f"SELECT id,name FROM graph_nodes WHERE id IN ({','.join('?' * len(orgs))})", tuple(orgs)).fetchall())
            for v in items:
                if v.get('org') in named:
                    v['org_name'] = named[v['org']]
        if name.startswith('projects'):
            for project in items:
                project['people'] = [n['name'] for n in neighbours(db, project['id'], 8) if n['kind'] == 'person'][:4]
                # Its board, stage, organization, value and expected close, for the list as a spreadsheet (CSV)
                pid = project['id'].removeprefix('project:')
                crm = db.execute('SELECT b.name board,p.stage,p.org,p.value,p.close_date FROM projects p LEFT JOIN boards b ON b.id=p.board_id WHERE p.id=?',
                                 (int(pid) if pid.isdigit() else -1,)).fetchone()
                if crm:
                    project.update({k: crm[k] for k in ('board', 'stage', 'value', 'close_date')}, org_domain=crm['org'])
    return {'items': items, 'sort': sort, 'next': str(offset + limit) if len(rows) > limit else None, 'total': list_counts(db)[name]}


def list_counts(db):
    """How long each Graph tab's list is, for its count; an empty tab is not shown."""
    found = {name: db.execute(f'SELECT COUNT(*) FROM graph_nodes WHERE {where}', args).fetchone()[0]
             for name, (where, args) in ((n, _paged_where(db, n)) for n in PAGED)}
    found['warmup'] = db.execute('SELECT COUNT(*) FROM hidden_domains WHERE hidden=1').fetchone()[0]
    return found


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


def resolve(db, node_id):
    """A node by its id, or the person an identifier is now part of (an old link after a merge)."""
    row = db.execute('SELECT * FROM graph_nodes WHERE id=?', (node_id,)).fetchone()
    if row or not re.fullmatch(r'(?:email|wa|slack|li|x|gh|manual):\S+', str(node_id or '')):
        return row
    return db.execute("SELECT n.* FROM graph_nodes n, json_each(n.info,'$.identifiers') j WHERE n.kind='person' AND j.value=? LIMIT 1",
                      (node_id,)).fetchone()


def same_people(db, limit=30):
    """Same person? The likely pairs the graph found, strongest first, for the review list in People."""
    out = []
    for r in db.execute('SELECT s.a,s.b,s.why FROM graph_same s JOIN graph_nodes x ON x.id=s.a JOIN graph_nodes y ON y.id=s.b '
                        'ORDER BY s.score DESC LIMIT ?', (limit,)):
        a, b = (node_view(db.execute('SELECT * FROM graph_nodes WHERE id=?', (i,)).fetchone()) for i in (r['a'], r['b']))
        out.append({'a': a, 'b': b, 'why': r['why']})
    return out


def detail(db, node_id, events=12):
    row = resolve(db, node_id)
    if not row:
        return None
    node_id = row['id']
    view = node_view(row, neighbours(db, node_id, 20))
    rows = db.execute('SELECT event_id,probable FROM graph_events WHERE node_id=? ORDER BY at DESC LIMIT ?', (node_id, events)).fetchall()
    if row['kind'] == 'org':  # an organization's timeline: the messages and meetings of the people in it
        rows = db.execute("SELECT g.event_id,MIN(g.probable) AS probable,MAX(g.at) AS at FROM graph_events g JOIN graph_links l ON l.a=g.node_id "
                          "WHERE l.b=? AND l.kind='member' GROUP BY g.event_id ORDER BY at DESC LIMIT ?", (node_id, events)).fetchall()
    view['event_ids'] = [r[0] for r in rows]
    view['probable_ids'] = [r[0] for r in rows if r[1]]
    view['open'] = open_between(db, node_id)
    said = db.execute('SELECT quote,event_id,created_at FROM person_roles WHERE identifier=?', (view.get('role_id') or node_id,)).fetchone()
    if said and view.get('role'):
        view.update(role_quote=said['quote'], role_event_id=said['event_id'], role_set_at=said['created_at'][:10])
    if row['kind'] == 'person':
        view['headline'] = headline(db, node_id) if json.loads(row['info'] or '{}').get('has_headline') else None
        own = enrichment.view_facts(db, json.loads(row['info'] or '{}').get('identifiers') or [node_id])  # their own words
        view['headline'] = view['headline'] or (own.get('headline') or {}).get('text')
        view['bio'] = own.get('bio')
        if view.get('org'):
            org = db.execute('SELECT name FROM graph_nodes WHERE id=?', (view['org'],)).fetchone()
            view['org_name'] = org['name'] if org else None
        if view.get('org_by') == 'review':
            ids = json.loads(row['info'] or '{}').get('identifiers') or [node_id]
            said_org = db.execute(f"SELECT quote FROM person_orgs WHERE identifier IN ({','.join('?' for _ in ids)}) AND event_id=?",
                                  (*ids, view.get('org_event_id'))).fetchone()
            view['org_quote'] = said_org['quote'] if said_org else None
    if row['kind'] == 'project':
        view['board'] = project_moves(db, node_id)
        try:
            view['project'] = project_card(db, node_id)
        except ValueError:
            view['project'] = None
    if row['kind'] == 'org':
        view['people_roles'] = [{'id': r['id'], 'name': r['name'], 'role': json.loads(r['info']).get('role')} for r in db.execute(
            "SELECT n.id,n.name,n.info FROM graph_links l JOIN graph_nodes n ON n.id=l.a WHERE l.b=? AND l.kind='member' ORDER BY n.strength DESC LIMIT 12", (node_id,))]
    return view



# The timeline on a person's, organization's or project's page: one list, newest first, from every source the Machine
# keeps. Their signals (mail, meetings, chats, Intercom), plus what the graph's links miss: Intercom conversations on
# their address, their Intercom contact or their organization's domain, a Zoom summary of a meeting they were in, the
# cards and promises that came from their signals, and their projects' moves. The owner's own view only: the team's
# Graph (shared_graph.py) never reads it.
TIMELINE_SOURCES = (('email', ('gmail.message', 'salesflare.email')),
                    ('meetings', ('calendar.event', 'salesflare.meeting', 'zoom.summary')),
                    ('intercom', ('intercom.conversation',)),
                    ('chats', ('whatsapp.message', 'slack.message', 'linkedin.message', 'x.dm', 'x.mention', 'telegram.message')))
TIMELINE_GROUPS = ('email', 'meetings', 'intercom', 'chats', 'cards', 'projects', 'other')
TIMELINE_CARDS = (*OPEN_CARD, 'completed')  # a dismissed card is not part of the story
ZOOM_NEAR = dt.timedelta(minutes=30)       # a Zoom summary within this of one of their meetings is that meeting's
_INTERCOM = {}


def timeline_source(kind):
    return next((name for name, kinds in TIMELINE_SOURCES if kind in kinds), 'other')


def intercom_index(db):
    """[(event_id, at, emails, contact_ids)] for every Intercom conversation, read once per change of the table."""
    key = (db.execute('PRAGMA database_list').fetchone()[2],
           *db.execute("SELECT COUNT(*),MAX(id),SUM(version) FROM events WHERE kind='intercom.conversation' AND deleted=0").fetchone())
    if key[0] and _INTERCOM.get('key') == key:
        return _INTERCOM['rows']
    rows = []
    for r in db.execute("SELECT id,occurred_at,metadata FROM events WHERE kind='intercom.conversation' AND deleted=0 ORDER BY occurred_at DESC LIMIT 20000"):
        try:
            m = json.loads(r['metadata'] or '{}')
        except ValueError:
            continue
        m = relayed('intercom.conversation', m) if isinstance(m, dict) else {}
        emails = {a for _, a in addresses(m.get('from'))[:1] if not relay(a)}
        if '@' in str(m.get('contact_email') or ''):
            emails.add(str(m['contact_email']).strip().lower())
        ids = {str(i) for i in (m.get('contact_ids') or ()) if str(i).strip()}
        rows.append((r['id'], r['occurred_at'], frozenset(emails), frozenset(ids)))
    _INTERCOM.clear()
    _INTERCOM.update(key=key, rows=rows)
    return rows


def _timeline_people(db, row):
    """The person nodes whose signals make up this page's timeline: the person, or an organization's people."""
    if row['kind'] == 'org':
        return [r[0] for r in db.execute("SELECT a FROM graph_links WHERE b=? AND kind='member'", (row['id'],))]
    return [row['id']]


def timeline(db, node_id, source='', cursor=0, limit=40):
    """One page of a person's, organization's or project's timeline: {'items', 'next', 'sources'}. An item is a signal
    ({'type': 'event', 'id': event id}), a card, a promise or a project's move, each with 'at' and its 'source' group
    (email, meetings, intercom, chats, cards, projects, other). source narrows it to one group; sources counts them all."""
    row = resolve(db, node_id)
    if not row:
        return None
    node, kind = row['id'], row['kind']
    info = json.loads(row['info'] or '{}')
    people = _timeline_people(db, row)
    signals = {}  # event id -> [at, kind, probable]

    def add(event_id, at, kind_, probable=0):
        have = signals.get(event_id)
        if have:
            have[2] = min(have[2], probable)
        else:
            signals[event_id] = [at, kind_, probable]
    on = people if kind != 'project' else [node]
    for start in range(0, len(on), 400):
        chunk = on[start:start + 400]
        for r in db.execute(f"SELECT g.event_id,MAX(g.at) at,MIN(g.probable) probable,e.kind FROM graph_events g JOIN events e ON e.id=g.event_id "
                            f"WHERE g.node_id IN ({','.join('?' * len(chunk))}) AND e.deleted=0 GROUP BY g.event_id", chunk):
            add(r['event_id'], r['at'], r['kind'], r['probable'])
    if kind in ('person', 'org'):
        # Intercom: on their address, on the Intercom contact that wrote from it, or (an organization) on its domain.
        emails = set()
        for p in people:
            got = db.execute('SELECT info FROM graph_nodes WHERE id=?', (p,)).fetchone()
            ids = (json.loads(got['info'] or '{}').get('identifiers') or []) if got else []
            emails |= {i[6:].lower() for i in [p, *ids] if str(i).startswith('email:')}
        domain = str(info.get('domain') or '').lower() if kind == 'org' else ''
        rows = intercom_index(db) if emails or domain else []
        contacts = {c for _, _, found, ids in rows if found & emails for c in ids}
        for event_id, at, found, ids in rows:
            if found & emails or ids & contacts or (domain and any(e.rsplit('@', 1)[1] == domain for e in found)):
                add(event_id, at, 'intercom.conversation')
        # Zoom: the summary of a meeting on their calendar, matched on when it started.
        meetings = sorted(parse_time(at) for at, k, _ in signals.values() if k in ('calendar.event', 'salesflare.meeting'))
        if meetings:
            for r in db.execute("SELECT id,occurred_at FROM events WHERE kind='zoom.summary' AND deleted=0 AND occurred_at>=? AND occurred_at<=?",
                                ((meetings[0] - ZOOM_NEAR).isoformat(), (meetings[-1] + ZOOM_NEAR).isoformat())):
                at = parse_time(r['occurred_at'])
                if any(abs(at - m) <= ZOOM_NEAR for m in meetings):
                    add(r['id'], r['occurred_at'], 'zoom.summary')
    items = [{'type': 'event', 'id': e, 'at': at, 'source': timeline_source(k), 'kind': k, 'probable': bool(p)} for e, (at, k, p) in signals.items()]
    ids = list(signals)
    try:
        cards, promised = {}, []
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            marks = ','.join('?' * len(chunk))
            for r in db.execute(f"SELECT DISTINCT s.id,s.title,s.status,s.created_at,s.due FROM suggestions s, json_each(s.event_ids) j WHERE j.value IN ({marks}) "
                                f"AND s.status IN ({','.join('?' * len(TIMELINE_CARDS))})", (*chunk, *TIMELINE_CARDS)):
                cards[r['id']] = r
            promised += db.execute(f"SELECT id,direction,who,what,due,status,created_at FROM promises WHERE event_id IN ({marks})", chunk).fetchall()
        if kind == 'org':  # a card the owner made about this organization (New card, Who)
            for r in db.execute("SELECT metadata FROM events WHERE kind='owner.card' AND deleted=0"):
                m = json.loads(r['metadata'] or '{}')
                if any(isinstance(a, dict) and a.get('id') == node for a in m.get('about') or ()) and m.get('card_id'):
                    got = db.execute(f"SELECT id,title,status,created_at,due FROM suggestions WHERE id=? AND status IN ({','.join('?' * len(TIMELINE_CARDS))})",
                                     (m['card_id'], *TIMELINE_CARDS)).fetchone()
                    if got:
                        cards[got['id']] = got
        items += [{'type': 'card', 'id': r['id'], 'at': r['created_at'], 'source': 'cards', 'title': r['title'], 'done': r['status'] == 'completed',
                   'due': r['due']} for r in cards.values()]
        items += [{'type': 'promise', 'id': r['id'], 'at': r['created_at'], 'source': 'cards', 'what': r['what'], 'who': r['who'],
                   'mine': r['direction'] == 'mine', 'status': r['status'], 'due': r['due']} for r in promised]
        # Projects: the moves of the projects they are on (an organization's: the projects for its domain).
        if kind == 'person':
            pids = {r[0] for r in db.execute("SELECT b FROM graph_links WHERE a=? AND kind='project'", (node,))}
            pids |= {'project:' + str(r[0]) for r in db.execute('SELECT project_id FROM project_people WHERE node_id=?', (node,))}
        elif kind == 'org':
            pids = {'project:' + str(r[0]) for r in db.execute('SELECT id FROM projects WHERE org=? COLLATE NOCASE', (str(info.get('domain') or node),))}
        else:
            pids = {node}
        nums = [int(p.split(':', 1)[1]) for p in pids if p.split(':', 1)[1].isdigit()]
        if nums:
            items += [{'type': 'move', 'id': 'move:' + str(r['id']), 'at': r['at'], 'source': 'projects', 'project': r['name'],
                       'project_id': 'project:' + str(r['project_id']), 'stage': r['stage'], 'by': r['source']}
                      for r in db.execute(f"SELECT m.id,m.project_id,m.stage,m.source,m.at,p.name FROM project_moves m JOIN projects p ON p.id=m.project_id "
                                          f"WHERE m.project_id IN ({','.join('?' * len(nums))})", nums)]
    except sqlite3.OperationalError:
        pass  # an older database without these tables yet
    counts = collections.Counter(i['source'] for i in items)
    if source and source != 'all':
        items = [i for i in items if i['source'] == source]
    items.sort(key=lambda i: (str(i['at'] or ''), str(i['id'])), reverse=True)
    offset, limit = max(0, int(cursor or 0)), max(1, min(int(limit or 40), 100))
    return {'items': items[offset:offset + limit], 'next': str(offset + limit) if len(items) > offset + limit else None,
            'sources': {g: counts[g] for g in TIMELINE_GROUPS if counts[g]}, 'total': sum(counts.values())}

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
        if item.get('side_by') != 'owner' and found['in'] and all(x['kind'] in ('family', 'friends') for x in found['in']):
            item['side'] = 'personal'  # Family and Friends are personal, whatever address they write from
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
        if row and row['kind'] == 'org':  # a card the owner made about this organization (New card, Who)
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
    they are to the owner, more when their organization pays the owner (billed through Stripe)."""
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
    """Say what someone (or an organization) is to the owner ("accountant", "Ann's partner", "customer at
    Globex", "accounting firm"). A review gives the message it read it in (event_id) and the line that
    shows it (quote, words from that message), so the owner can confirm it. The owner's word stays until
    the owner changes it; the owner saying the same role confirms it; an empty role from the owner
    clears it. When the owner replaces a review's role, the correction is kept for later reviews to learn from."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    row = db.execute("SELECT id,kind,name,info FROM graph_nodes WHERE id=? AND kind IN ('person','org')", (str(node_id),)).fetchone()
    if not row:
        raise ValueError('role needs a person or organization node_id from graph results')
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
            raise ValueError('Unknown organization node_id; give its name instead')
        org, org_id = found['name'], found['id']
    if len(org) > 100 or (not org and source != 'owner'):
        raise ValueError('org is the organization\'s name as the source states it ("Acme"), up to 100 characters, or an org:… node_id')
    current = db.execute('SELECT * FROM person_orgs WHERE identifier=?', (row['id'],)).fetchone()
    if current and current['source'] == 'owner' and source != 'owner':
        return {'org': current['org'] or None, 'kept': 'The owner set this organization; it stays.'}
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
    """Say an organization's mail is an email warm-up tool's traffic (templated exchanges between inboxes that
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
        raise ValueError('warmup needs an organization node_id (org:…) with an email domain from graph results')
    if current and current['source'] == 'owner' and source != 'owner':
        return {'warmup': bool(current['hidden']), 'kept': 'The owner decided about this organization; it stays.'}
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
    was set (now billed through Stripe, a new organization), or saved without the line it came from. The
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
    """The owner's name for a person, organization or project. A project renamed to another project's
    name joins it; an empty name gives a person or organization back the name from their messages."""
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
        # Its place on a board goes along when the other has none; its moves join the other's.
        cols = ','.join(f'{c.split()[0]}=(SELECT {c.split()[0]} FROM projects WHERE id=:old)' for c in PROJECT_STAGE_COLUMNS)
        db.execute(f'UPDATE projects SET {cols} WHERE id=:new AND board_id IS NULL', {'old': row['id'], 'new': other['id']})
        db.execute('UPDATE project_moves SET project_id=? WHERE project_id=?', (other['id'], row['id']))
        db.execute('DELETE FROM projects WHERE id=?', (row['id'],))
        return {'node_id': 'project:' + str(other['id']), 'name': other['name'], 'merged': True}
    if not db.execute("SELECT 1 FROM graph_nodes WHERE id=? AND kind IN ('person','org')", (node_id,)).fetchone():
        raise ValueError('Rename a person, organization or project from graph results')
    if len(name) > 100:
        raise ValueError('A name is up to 100 characters')
    if name:
        db.execute('INSERT OR REPLACE INTO graph_names(node_id,name,created_at) VALUES(?,?,?)', (node_id, name, now))
    else:
        db.execute('DELETE FROM graph_names WHERE node_id=?', (node_id,))
    return {'node_id': node_id, 'name': name or None}


def website_domain(text):
    """The domain in what the owner typed as a website ("https://www.acme.com/about" is acme.com), or None."""
    host = re.sub(r'^[a-z]+://', '', str(text or '').strip().lower()).split('/', 1)[0].split('?', 1)[0].removeprefix('www.')
    return host if ORG_DOMAIN.fullmatch(host) else None


def create_record(db, kind, args, now):
    """A person or organization the owner adds by hand, before (or without) any message from them. A person has a
    name, and optionally an email address and where they work; an organization a name and optionally its website.
    One that is already in the graph (the same address, website or name for an organization) is not made twice:
    {'node_id', 'name', 'existing': True} points to it. Otherwise {'node_id', 'name', 'created': True}; the caller
    rebuilds the graph, which keeps them from then on (manual_nodes), also with no messages."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    args = args if isinstance(args, dict) else {}
    name = ' '.join(str(args.get('name') or '').split())
    if not name:
        raise ValueError('Give them a name')
    if len(name) > 100:
        raise ValueError('A name is up to 100 characters')
    if kind == 'person':
        email = str(args.get('email') or '').strip().lower()
        if email and not EMAIL.fullmatch(email):
            raise ValueError(f'{email} is not an email address')
        org = ' '.join(str(args.get('org') or '').split())
        if len(org) > 100:
            raise ValueError('An organization name is up to 100 characters')
        identifier = 'email:' + email if email else 'manual:' + secrets.token_hex(8)
        found = resolve(db, identifier) if email else None
        if found:
            return {'node_id': found['id'], 'name': found['name'], 'existing': True}
        salt = _salt(db)
        if salt:  # deleted before, added back by the owner: no longer kept out
            db.execute('DELETE FROM forgotten_people WHERE digest=?', (forgotten_digest(salt, identifier),))
        org_id = None
        if org.startswith('org:'):
            row = db.execute("SELECT id,name FROM graph_nodes WHERE id=? AND kind='org'", (org,)).fetchone()
            org, org_id = (row['name'], row['id']) if row else ('', None)
        elif org:
            row = db.execute("SELECT id FROM graph_nodes WHERE kind='org' AND name=? COLLATE NOCASE ORDER BY strength DESC LIMIT 1", (org,)).fetchone()
            org_id = row['id'] if row else None
        if org:
            db.execute('INSERT OR REPLACE INTO person_orgs(identifier,org,org_id,source,via,event_id,quote,said_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (identifier, org, org_id, 'owner', None, None, None, now, now))
    elif kind == 'org':
        site = str(args.get('website') or '').strip()
        domain = website_domain(site)
        if site and not domain:
            raise ValueError(f'{site} is not a website, like acme.com')
        identifier = 'org:' + domain if domain else 'org:named:' + '-'.join(folded_words(name).split())[:80]
        if identifier == 'org:named:':
            raise ValueError('Give it a name with letters or digits')
        found = db.execute("SELECT id,name FROM graph_nodes WHERE kind='org' AND (id=? OR name=? COLLATE NOCASE) ORDER BY id=? DESC LIMIT 1",
                           (identifier, name, identifier)).fetchone()
        if found:
            return {'node_id': found['id'], 'name': found['name'], 'existing': True}
    else:
        raise ValueError('kind is person or org')
    db.execute('INSERT OR REPLACE INTO manual_nodes(identifier,kind,name,created_at) VALUES(?,?,?,?)', (identifier, kind, name, now))
    db.execute('INSERT OR REPLACE INTO graph_names(node_id,name,created_at) VALUES(?,?,?)', (identifier, name, now))
    return {'node_id': identifier, 'name': name, 'created': True}


def delete_org(db, node_id):
    """Deletes an organization the owner added by hand, while nobody is in it. One built from people's addresses
    comes from their messages, so it stays."""
    node_id = str(node_id or '')
    if not db.execute("SELECT 1 FROM manual_nodes WHERE identifier=? AND kind='org'", (node_id,)).fetchone():
        raise ValueError('Only an organization you added yourself can be deleted')
    if db.execute("SELECT 1 FROM graph_links WHERE b=? AND kind='member'", (node_id,)).fetchone():
        raise ValueError('People are in it: change their organization first')
    name = db.execute('SELECT name FROM graph_nodes WHERE id=?', (node_id,)).fetchone()
    db.execute('DELETE FROM manual_nodes WHERE identifier=?', (node_id,))
    db.execute('DELETE FROM graph_names WHERE node_id=?', (node_id,))
    db.execute('DELETE FROM graph_nodes WHERE id=?', (node_id,))
    db.execute('DELETE FROM org_edits WHERE domain=?', (node_id if node_id.startswith('org:named:') else node_id[4:],))
    return {'deleted': name['name'] if name else node_id}


ORG_FIELDS = {'website': 200, 'linkedin_url': 200, 'industry': 120, 'size': 40, 'location': 120, 'founded': 4, 'about': 600, 'notes': 4000}


def notes_text(value):
    """The owner's notes as typed: lines kept, spaces at their ends and runs of empty lines taken out."""
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(line.rstrip() for line in str(value or '').replace('\r\n', '\n').split('\n'))).strip()


def org_domain(db, node_id):
    row = db.execute("SELECT info FROM graph_nodes WHERE id=? AND kind='org'", (str(node_id or ''),)).fetchone()
    if row and str(node_id).startswith('org:named:'):
        return str(node_id)  # one with no website: its details are kept under its id
    if not row or not json.loads(row['info']).get('domain'):
        raise ValueError('Edit an organization from graph results')
    return json.loads(row['info'])['domain']


def org_edit(db, node_id, fields, now):
    """The owner's own details for an organization. They win over what an enrichment service says and survive
    every refresh; an empty field gives that one back to the service."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    domain, fields = org_domain(db, node_id), fields if isinstance(fields, dict) else {}
    row = db.execute('SELECT fields FROM org_edits WHERE domain=?', (domain,)).fetchone()
    kept = json.loads(row['fields']) if row else {}
    for field, limit in ORG_FIELDS.items():
        if field not in fields:
            continue
        text = notes_text(fields[field]) if field == 'notes' else ' '.join(str(fields[field] or '').split())
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
    return {'node_id': domain if domain.startswith('org:') else 'org:' + domain, 'edited': sorted(kept)}


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


# The owner's own details for a person (title, location, LinkedIn, phones, emails, Work or Personal). They win over what
# an enrichment service or the messages say and stay through every rebuild. Each field is kept on the identity whose page
# it was saved on, so it stays through a merge (the joined person carries it) and an Undo (it stays with that identity).
# Phones and emails are kept as what the owner added and took off, so an address that turns up later still shows.
PERSON_FIELDS = {'title': 120, 'location': 120, 'linkedin_url': 200, 'side': 8, 'notes': 4000}
PERSON_LISTS = {'phones': 40, 'emails': 200}
PHONE = re.compile(r'\+?[\d\s().-]{6,40}')
EMAIL = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')


def contact_key(kind, value):
    return re.sub(r'\D', '', value) if kind == 'phones' else value.casefold()


def found_contacts(identifiers):
    """Phones and emails as the messages show them: their addresses and WhatsApp numbers."""
    ids = sorted(identifiers or ())
    return {'emails': [i.split(':', 1)[1] for i in ids if i.startswith('email:')],
            'phones': ['+' + i.split(':', 1)[1] for i in ids if i.startswith('wa:') and i.split(':', 1)[1].isdigit()]}


def shown_contacts(identifiers, edits):
    """[{'value', 'by'}] per kind: what the messages show, less what the owner took off, then what they added."""
    found, out = found_contacts(identifiers), {}
    for kind in PERSON_LISTS:
        kept = (edits or {}).get(kind) or {}
        gone = {contact_key(kind, v) for v in kept.get('removed', ())}
        rows = [{'value': v, 'by': 'messages'} for v in found[kind] if contact_key(kind, v) not in gone]
        seen = {contact_key(kind, r['value']) for r in rows}
        for v in kept.get('added', ()):
            if contact_key(kind, v) not in seen:
                rows.append({'value': v, 'by': 'owner'})
                seen.add(contact_key(kind, v))
        out[kind] = rows
    return out


def person_edits_for(db, group):
    """The owner's edits for a person whose identities are group, oldest first so a later save wins."""
    group = sorted(group or ())
    if not group:
        return {}
    kept = {}
    for r in db.execute(f"SELECT fields FROM person_edits WHERE identifier IN ({','.join('?' for _ in group)}) ORDER BY updated_at,identifier", group):
        kept.update(json.loads(r['fields']))
    return kept


def person_edit(db, node_id, fields, now):
    """Save the owner's details for a person: only the fields given change; an empty field gives that one back to the
    looked-up value (or, for phones and emails, the list back to what the messages show)."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    node_id, fields = str(node_id or ''), fields if isinstance(fields, dict) else {}
    row = db.execute("SELECT info FROM graph_nodes WHERE id=? AND kind='person'", (node_id,)).fetchone()
    if not row:
        raise ValueError('Edit a person from graph results')
    group = set(json.loads(row['info']).get('identifiers') or ()) | {node_id}
    rows = {r['identifier']: json.loads(r['fields']) for r in db.execute(
        f"SELECT identifier,fields FROM person_edits WHERE identifier IN ({','.join('?' for _ in group)})", sorted(group))}
    mine = rows.get(node_id, {})
    found = found_contacts(group)
    stated_phone = ' '.join(str((json.loads(row['info']).get('profile') or {}).get('phone') or '').split())
    if PHONE.fullmatch(stated_phone):
        found['phones'].append(stated_phone)  # a number a connector read can be taken off too
    for field, limit in {**PERSON_FIELDS, **PERSON_LISTS}.items():
        if field not in fields:
            continue
        if field in PERSON_LISTS:
            given = fields[field] if isinstance(fields[field], list) else str(fields[field] or '').splitlines()
            values, keys = [], set()
            for v in given:
                text = ' '.join(str(v or '').split())
                if not text:
                    continue
                if len(text) > limit or not (PHONE if field == 'phones' else EMAIL).fullmatch(text):
                    raise ValueError(f'{text[:60]} is not {"a phone number" if field == "phones" else "an email address"}')
                if contact_key(field, text) not in keys:
                    keys.add(contact_key(field, text))
                    values.append(text)
            if len(values) > 10:
                raise ValueError(f'Up to 10 {field}')
            known = {contact_key(field, v) for v in found[field]}
            value = {'added': [v for v in values if contact_key(field, v) not in known],
                     'removed': [v for v in found[field] if contact_key(field, v) not in keys]}
            value = value if value['added'] or value['removed'] else None
        else:
            value = notes_text(fields[field]) if field == 'notes' else ' '.join(str(fields[field] or '').split())
            if field == 'side':
                if value not in ('', 'work', 'personal'):
                    raise ValueError('Work or Personal')
            elif len(value) > limit:
                raise ValueError(f"{'LinkedIn' if field == 'linkedin_url' else field.capitalize()} is up to {limit} characters")
            elif field == 'linkedin_url' and value:
                value = value if re.match(r'https?://', value) else 'https://' + value
                if not re.match(r'https://([a-z]{2,3}\.)?linkedin\.com/', value):
                    raise ValueError('LinkedIn is a linkedin.com address, like linkedin.com/in/their-name')
            value = value or None
        for other, kept in rows.items():  # one place per field: the page it was last saved on
            if other != node_id:
                kept.pop(field, None)
        if value is None:
            mine.pop(field, None)
        else:
            mine[field] = value
    rows[node_id] = mine
    for identifier, kept in rows.items():
        if kept:
            db.execute('INSERT INTO person_edits(identifier,fields,updated_at) VALUES(?,?,?) ON CONFLICT(identifier) DO UPDATE SET '
                       'fields=excluded.fields,updated_at=CASE WHEN identifier=? THEN excluded.updated_at ELSE person_edits.updated_at END',
                       (identifier, json.dumps(kept), now, node_id))
        else:
            db.execute('DELETE FROM person_edits WHERE identifier=?', (identifier,))
    return {'node_id': node_id, 'edited': sorted(mine)}


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


def one_line(text, limit):
    """Text as one tidy line; None when it is longer than limit (the caller says what fits)."""
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return text if len(text) <= limit else None


def board_row(db, name, stages, source, now):
    """The board with this name, made with these stages (in order) when it is new. "Stalled or declined" is always
    its last row. A board keeps the stages it was made with; its projects stand in them."""
    name = one_line(name, 60)
    if not name:
        raise ValueError('A board name is a short label (up to 60 characters), like "Partner listings"')
    row = db.execute('SELECT * FROM boards WHERE name=? COLLATE NOCASE', (name,)).fetchone()
    if row:
        return row
    if source != 'owner':  # nothing is a board until the owner saves it
        raise ValueError(f'No board named "{name}". Suggest one with graph suggest_board; it becomes a board once the owner saves it')
    cleaned = []
    for stage in stages if isinstance(stages, list) else ():
        item = stage if isinstance(stage, dict) else {'name': stage}
        title, hint = one_line(item.get('name'), 30), one_line(item.get('hint'), 80)
        if not title or title.casefold() == STALLED.casefold() or title.casefold() in {c['name'].casefold() for c in cleaned}:
            raise ValueError(f'Stages are distinct short names (up to 30 characters); "{STALLED}" is always there')
        cleaned.append({'name': title, **({'hint': hint} if hint else {})})
    if not 2 <= len(cleaned) <= 7:
        raise ValueError(f'A new board needs stages: 2 to 7 names in order, like ["Contacted", "Agreed", "Live"]; "{STALLED}" is always there')
    board_id = db.execute('INSERT INTO boards(name,stages,source,created_at) VALUES(?,?,?,?)', (name, json.dumps(cleaned), source, now)).lastrowid
    return db.execute('SELECT * FROM boards WHERE id=?', (board_id,)).fetchone()


def stage_names(board):
    """A board's stages in order, with "Stalled or declined" last when the board has that row."""
    return [s['name'] for s in json.loads(board['stages'])] + ([STALLED] if board['side'] else [])


ORG_DOMAIN = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}')


def set_stage(db, project, stage, source, now, board=None, stages=None, reason=None, event_id=None, next_step=None, org=None):
    """Puts a project on a board, or moves it to another stage there. A review or agent move (source 'review' or
    'agent') gives the message that shows it (event_id, which also labels it into the project) and why, in a short
    clause; the owner's own move (source 'owner', a drag on the dashboard) needs neither and wins over the others
    until a message newer than it moves the project again. An older message never undoes a newer move.
    project: a name (made when new) or a project node id. Returns where it stands, with kept when nothing moved."""
    owner = source == 'owner'
    if str(project or '').startswith('project:'):
        row = db.execute('SELECT * FROM projects WHERE id=?', (str(project).split(':', 1)[1],)).fetchone()
        if not row:
            raise ValueError('Unknown project')
    else:
        if owner:
            raise ValueError('Move a project from its node_id')
        row = db.execute('SELECT * FROM projects WHERE id=?', (project_id(db, project, now),)).fetchone()
    if board:
        target = board_row(db, board, stages, source, now)
    elif row['board_id']:
        target = db.execute('SELECT * FROM boards WHERE id=?', (row['board_id'],)).fetchone()
    else:
        target = None
    if not target:
        raise ValueError('Say which board the project is on (board; with stages when the board is new)')
    names = stage_names(target)
    wanted = one_line(stage, 40) or ''
    match = next((n for n in names if n.casefold() == wanted.casefold()), None) if wanted else (
        row['stage'] if row['board_id'] == target['id'] and row['stage'] else names[0])
    if not match:
        raise ValueError(f"Stages on {target['name']}: " + ', '.join(names))
    at = now
    if not owner:
        found = db.execute('SELECT occurred_at FROM events WHERE id=? AND deleted=0', (int(event_id),)).fetchone() \
            if str(event_id or '').isdigit() else None
        if not found:
            raise ValueError('A move gives the event_id of the message that shows it')
        reason = one_line(reason, 160)
        if not reason:
            raise ValueError('reason: what happened, one short clause (up to 160 characters), like "Sam signed the order form"')
        at, event_id = found['occurred_at'], int(event_id)
        label(db, [event_id], row['name'], source, now)
    else:
        reason, event_id = None, None
    moved = row['board_id'] != target['id'] or (row['stage'] or '').casefold() != match.casefold() or not row['stage_by']  # placing one the owner put on the board counts
    kept = None
    if not owner:
        if row['stage_by'] == 'owner' and row['stage_at'] and parse_time(at) <= parse_time(row['stage_at']):
            kept = 'The owner moved it after this message'
        elif row['stage_at'] and parse_time(at) < parse_time(row['stage_at']):
            kept = 'A later message moved it already'
        elif row['stage_event_id']:
            last = db.execute('SELECT occurred_at FROM events WHERE id=?', (row['stage_event_id'],)).fetchone()
            if last and parse_time(at) < parse_time(last['occurred_at']):
                kept = 'A later message moved it already'
    if moved and not kept:
        db.execute('INSERT INTO project_moves(project_id,board_id,stage,reason,event_id,source,at,created_at) VALUES(?,?,?,?,?,?,?,?)',
                   (row['id'], target['id'], match, reason, event_id, source, at, now))
        db.execute('UPDATE projects SET board_id=?,stage=?,stage_reason=?,stage_event_id=?,stage_by=?,stage_at=? WHERE id=?',
                   (target['id'], match, reason, event_id, 'owner' if owner else 'review', at, row['id']))
        if owner:  # a move by hand reopens a won or lost one; Won or Lost sets it again after its own move
            db.execute('UPDATE projects SET outcome=NULL,outcome_reason=NULL WHERE id=?', (row['id'],))
    if not owner and not kept and next_step is not None:
        step = one_line(next_step, 120)
        if step is None:
            raise ValueError('next: the owner\'s next step in a few words (up to 120 characters)')
        db.execute('UPDATE projects SET next_step=? WHERE id=?', (step or None, row['id']))
    if org and not kept:
        domain = re.sub(r'^(?:https?://)?(?:www\.)?', '', str(org).strip().lower()).split('/')[0]
        if not ORG_DOMAIN.fullmatch(domain):
            raise ValueError('org: the organization\'s web domain, like "example.com"')
        db.execute('UPDATE projects SET org=? WHERE id=?', (domain, row['id']))
    if not owner and not moved and not kept and (next_step is not None or org):
        # The stage stayed the same, but its next step/details have newer evidence. Keep that watermark too;
        # a message between the last move and this update must not put the older details back.
        db.execute('UPDATE projects SET stage_at=? WHERE id=?', (at, row['id']))
    return {'node_id': 'project:' + str(row['id']), 'project': row['name'], 'board': target['name'],
            'stage': row['stage'] if kept else match, **({'kept': kept} if kept else {})}


def boards_brief(db, limit=150):
    """The boards and where each project on them stands, for a review to move them: [{board, stages, projects}]."""
    out = []
    for b in db.execute('SELECT * FROM boards ORDER BY id'):
        rows = db.execute('SELECT name,stage FROM projects WHERE board_id=? AND closed_at IS NULL ORDER BY coalesce(stage_at,created_at) DESC LIMIT ?',
                          (b['id'], limit)).fetchall()
        out.append({'board': b['name'], 'stages': stage_names(b), 'projects': {r['name']: r['stage'] for r in rows},
                    **({'about': b['about']} if b['about'] else {})})
    return out


def project_events(db, pid, limit, until=None):
    """A project's latest messages, newest first: the ones labeled into it and the ones the graph links to it."""
    return db.execute('SELECT id,kind,occurred_at FROM events WHERE deleted=0 AND id IN (SELECT event_id FROM event_projects WHERE project_id=? '
                      "UNION SELECT event_id FROM graph_events WHERE node_id=? AND probable=0)" + (' AND occurred_at<=?' if until else '') +
                      ' ORDER BY occurred_at DESC LIMIT ?', (pid, 'project:' + str(pid), *((until,) if until else ()), limit)).fetchall()


def clean_visibility(value, allow_none=False):
    """'only_you' or 'team' (None too when allowed: a project that follows its board)."""
    if value is None and allow_none:
        return None
    text = str(value or '').strip().lower().replace(' ', '_')
    if text not in VISIBILITY:
        raise ValueError('visibility is "only_you" or "team"' + (' (or null: as its board)' if allow_none else ''))
    return text


def hidden_project_ids(db):
    """Projects only the owner sees: their own visibility, else their board's, is 'only_you'. A project on no board in
    an open Only-you suggestion (an M&A board not saved yet) is held back too. They, their board and their timelines
    never go to the Team Graph. A stable hook for the sharing code."""
    return {r[0] for r in db.execute("SELECT p.id FROM projects p LEFT JOIN boards b ON b.id=p.board_id "
                                     "WHERE coalesce(p.visibility,b.visibility,'team')='only_you' OR (p.board_id IS NULL AND p.visibility IS NULL "
                                     "AND p.id IN (SELECT j.value FROM board_suggestions s,json_each(s.projects) j WHERE s.state='open' AND s.visibility='only_you'))")}


def hidden_event_ids(db):
    """Messages of Only-you projects (labeled into one, or linked by the graph): their threads stay Only you."""
    ids = sorted(hidden_project_ids(db))
    if not ids:
        return set()
    marks = ','.join('?' * len(ids))
    return {r[0] for r in db.execute(f'SELECT event_id FROM event_projects WHERE project_id IN ({marks}) UNION '
                                     f'SELECT event_id FROM graph_events WHERE probable=0 AND node_id IN ({marks})',
                                     (*ids, *('project:' + str(i) for i in ids)))}


def visible_to_team(db, node_id):
    """False for an Only-you project (by its own visibility or its board's); True for anything else, which the sharing
    rules then decide on."""
    node = str(node_id or '')
    return not (node.startswith('project:') and node[8:].isdigit() and int(node[8:]) in hidden_project_ids(db))


def team_board_ids(db):
    """Boards the team sees (visibility 'team'; older boards without one too)."""
    return {r[0] for r in db.execute("SELECT id FROM boards WHERE coalesce(visibility,'team')='team'")}


def set_project_visibility(db, node_id, visibility):
    """The owner's override for one project: 'only_you', 'team', or None to follow its board."""
    node = str(node_id or '')
    row = db.execute('SELECT id FROM projects WHERE id=?', (node[8:],)).fetchone() if node.startswith('project:') and node[8:].isdigit() else None
    if not row:
        raise ValueError('Unknown project')
    value = clean_visibility(visibility, allow_none=True)
    db.execute('UPDATE projects SET visibility=? WHERE id=?', (value, row['id']))
    return {'node_id': node, 'visibility': value}


def unsorted_projects(db, limit=None):
    """Open projects the owner put on a board that no review or move has placed yet and no review has
    answered for, while reviews still try (fewer than SORT_TRIES got them): what the next review sorts first."""
    return db.execute('SELECT p.*,b.name board FROM projects p JOIN boards b ON b.id=p.board_id WHERE p.closed_at IS NULL AND p.stage_by IS NULL '
                      'AND p.sort_note IS NULL AND p.sort_tries<? ORDER BY p.sort_tries,p.id' + (' LIMIT ?' if limit else ''),
                      (SORT_TRIES, *((limit,) if limit else ()))).fetchall()


def sort_brief(db, run_id, limit=SORT_BATCH, per=SORT_MESSAGES):
    """The "Boards to sort" block for review run_id: each unplaced project with its board, stages and latest
    messages (event_ids to read). Marks them as given to this run, so finish_analysis can hold it to an answer
    (sort_answer) and the tile can tell a failed run. A project with no messages at all gets its note now."""
    lines = []
    for p in unsorted_projects(db, limit):
        node = 'project:' + str(p['id'])
        events = project_events(db, p['id'], per)
        if not events:
            db.execute("UPDATE projects SET sort_note='No messages about it yet',sort_run=? WHERE id=?", (run_id, p['id']))
            continue
        board = db.execute('SELECT * FROM boards WHERE id=?', (p['board_id'],)).fetchone()
        db.execute('UPDATE projects SET sort_run=?,sort_tries=sort_tries+1 WHERE id=?', (run_id, p['id']))
        said = ', '.join(f"{e['id']} ({e['kind']}, {str(e['occurred_at'])[:10]})" for e in events)
        lines.append(f"- {p['name']} ({node}) on {p['board']} ({' > '.join(stage_names(board))}); its latest messages, newest first: {said}")
    return '\n'.join(lines)


def sort_answer(db, run_id, answer):
    """A review that got "Boards to sort" answers for each project before it finishes: placed with graph (stage,
    event_id, reason), or in finish_analysis sorted [{"project": node_id, "why"}] when no message shows where it
    stands. Raises while one is left; returns how many it couldn't place."""
    given = db.execute('SELECT id,name FROM projects WHERE sort_run=? AND stage_by IS NULL AND sort_note IS NULL AND board_id IS NOT NULL '
                       'AND closed_at IS NULL', (run_id,)).fetchall()
    if not given:
        return 0
    if answer is not None and not (isinstance(answer, list) and all(isinstance(a, dict) for a in answer)):
        raise ValueError('sorted is [{"project": "project:<id>", "why": "<a few words>"}]')
    why = {}
    for a in answer or []:
        ref, text = str(a.get('project') or ''), one_line(a.get('why'), 120)
        if not text:
            raise ValueError('sorted: each one says why no message shows where it stands, in a few words')
        why[ref.casefold()] = text
    left, done = [], 0
    for r in given:
        text = why.get(('project:' + str(r['id'])).casefold()) or why.get(r['name'].casefold())
        if text:
            db.execute('UPDATE projects SET sort_note=? WHERE id=?', (text, r['id']))
            done += 1
        else:
            left.append(f"{r['name']} (project:{r['id']})")
    if left:
        raise ValueError('Boards to sort: place ' + ', '.join(left) + ' with graph (project, board, stage, event_id, reason), the first stage when '
                         'nothing has moved it further; or add it to finish_analysis sorted [{"project", "why"}] when no message shows where it stands')
    return done


def sort_view(db, p):
    """The tile's sorting fields: sorting (a review is due or at it), sort_note (why a review couldn't place it) or
    sort_failed (what went wrong); all off once the project is placed."""
    if p['stage_by']:
        return {'sorting': False}
    state, text = sort_state(db, p)
    return {'sorting': state == 'sorting', **({'sort_note': text} if state == 'note' else {}), **({'sort_failed': text} if state == 'failed' else {})}


def sort_state(db, p):
    """How an unplaced project's tile reads: ('sorting', None) while a review is due or at it, ('note', why) when a
    review couldn't place it, ('failed', what happened) when the reviews that got it failed or gave up."""
    if p['sort_note']:
        return 'note', p['sort_note']
    run = None
    if p['sort_run']:
        try:
            run = db.execute('SELECT status FROM review_runs WHERE run_id=?', (p['sort_run'],)).fetchone()
        except sqlite3.OperationalError:
            run = None
    if (p['sort_tries'] or 0) >= SORT_TRIES:
        return 'failed', f"Couldn't sort it in {p['sort_tries']} reviews"
    if run and run['status'] not in ('starting', 'running', 'recovering', 'ok'):
        return 'failed', 'Sorting failed, trying again'
    return 'sorting', None


def clean_stages(stages):
    """[{name, hint?, was?}] from names or dicts: distinct short names, 1 to MAX_STAGES, never the Stalled row itself."""
    cleaned = []
    for stage in stages if isinstance(stages, list) else ():
        item = stage if isinstance(stage, dict) else {'name': stage}
        title, hint = one_line(item.get('name'), 30), one_line(item.get('hint'), 80)
        if not title or title.casefold() == STALLED.casefold() or title.casefold() in {c['name'].casefold() for c in cleaned}:
            raise ValueError(f'Stages are distinct short names (up to 30 characters); "{STALLED}" is its own option')
        cleaned.append({'name': title, **({'hint': hint} if hint else {}), **({'was': str(item['was'])} if item.get('was') else {})})
    if not 1 <= len(cleaned) <= MAX_STAGES:
        raise ValueError(f'A board has 1 to {MAX_STAGES} stages, in order')
    return cleaned


def open_project_rows(db, ids):
    """Projects by node id or name, open ones only, in the order given."""
    out, seen = [], set()
    for ref in ids if isinstance(ids, list) else ():
        ref = str(ref or '')
        row = db.execute('SELECT * FROM projects WHERE id=? AND closed_at IS NULL', (ref.split(':', 1)[1],)).fetchone() if ref.startswith('project:') and ref[8:].isdigit() \
            else db.execute('SELECT * FROM projects WHERE name=? COLLATE NOCASE AND closed_at IS NULL', (ref.strip(),)).fetchone()
        if row and row['id'] not in seen:
            seen.add(row['id']); out.append(row)
    return out


def suggest_board(db, args, source, now):
    """A review's suggestion: a board for a group of open projects of the same kind of work, with stages that fit
    that work. It stays a suggestion until the owner saves it (save_board) or says not now."""
    name = one_line(args.get('name'), 60)
    if not name:
        raise ValueError('name: what the projects are, a short label like "Partner listings"')
    if db.execute('SELECT 1 FROM boards WHERE name=? COLLATE NOCASE', (name,)).fetchone() or \
            db.execute('SELECT 1 FROM board_suggestions WHERE name=? COLLATE NOCASE', (name,)).fetchone():
        raise ValueError(f'"{name}" is a board or was suggested already')
    if db.execute("SELECT count(*) FROM board_suggestions WHERE state='open'").fetchone()[0] >= MAX_OPEN_SUGGESTIONS:
        raise ValueError(f'{MAX_OPEN_SUGGESTIONS} board suggestions are open already: wait until the owner decides')
    if args.get('visibility') is None:
        raise ValueError('visibility: "only_you" for sensitive work (M&A, investors, legal, health, hiring a named person), "team" for '
                         'customer, partner and other shared pipelines')
    visibility = clean_visibility(args.get('visibility'))
    stages = [{k: v for k, v in s.items() if k != 'was'} for s in clean_stages(args.get('stages'))]
    if len(stages) < 2:
        raise ValueError('stages: 2 or more, in the order this kind of work goes')
    taken = {i for (p,) in db.execute("SELECT projects FROM board_suggestions WHERE state='open'") for i in json.loads(p)}
    rows = [r for r in open_project_rows(db, args.get('projects')) if not r['board_id'] and r['id'] not in taken]
    if len(rows) < 3:
        raise ValueError('projects: 3 or more open projects on no board, the same kind of work')
    about = one_line(args.get('about'), 200)
    if not about:
        raise ValueError('about: one sentence on which projects belong (up to 200 characters)')
    sid = db.execute('INSERT INTO board_suggestions(name,stages,about,projects,reason,source,created_at,visibility) VALUES(?,?,?,?,?,?,?,?)',
                     (name, json.dumps(stages), about, json.dumps([r['id'] for r in rows]), one_line(args.get('reason'), 160), source, now,
                      visibility)).lastrowid
    return {'suggestion_id': sid, 'name': name, 'projects': len(rows), 'visibility': visibility,
            'status': 'suggested: it becomes a board once the owner saves it'}


def board_check_answer(db, run_id, answer):
    """A review that got "Boards to suggest" answers it before it finishes: "suggested" once it called suggest_board,
    or "none: why". None when this run has no open check."""
    row = db.execute('SELECT offered_at FROM board_offers WHERE id=1 AND run_id=? AND answer IS NULL', (run_id,)).fetchone()
    if not row:
        return None
    text = one_line(answer, 200)
    if not text:
        raise ValueError('This review has "Boards to suggest": look over those projects, then finish with boards: "suggested" '
                         '(after graph suggest_board) or "none: <why, in a few words>"')
    if text.casefold().startswith('suggested') and not db.execute('SELECT 1 FROM board_suggestions WHERE created_at>=?', (row['offered_at'],)).fetchone():
        raise ValueError('No board was suggested in this review: call graph suggest_board first, or finish with boards: "none: <why>"')
    db.execute('UPDATE board_offers SET answer=? WHERE id=1', (text,))
    return text


def board_suggestions(db):
    """Open suggestions for the Projects page, newest first, with their projects still open and on no board."""
    out = []
    for r in db.execute("SELECT * FROM board_suggestions WHERE state='open' ORDER BY id DESC LIMIT 3").fetchall():
        rows = [p for p in open_project_rows(db, ['project:' + str(i) for i in json.loads(r['projects'])]) if not p['board_id']]
        if rows:
            out.append({'id': r['id'], 'name': r['name'], 'stages': json.loads(r['stages']), 'about': r['about'], 'reason': r['reason'],
                        'visibility': r['visibility'] or 'team', 'projects': [{'id': 'project:' + str(p['id']), 'name': p['name']} for p in rows]})
    return out


def dismiss_board_suggestion(db, suggestion_id, now):
    found = db.execute("UPDATE board_suggestions SET state='dismissed',decided_at=? WHERE id=? AND state='open'", (now, int(suggestion_id or 0))).rowcount
    if not found:
        raise ValueError('That suggestion is gone')
    return {'status': 'dismissed'}


def save_board(db, args, now):
    """The owner's board, new (from scratch or a suggestion) or edited: its name, stages in order (a renamed one
    carries `was`, its old name), whether it has the Stalled row, which projects belong (about, a sentence) and the
    projects on it. A project put on it waits for a review to place it by its messages (sorting); one taken off
    leaves the board. A removed stage's projects go to the nearest earlier stage that stays; without the Stalled row
    its projects leave the board. Returns the board and what moved, to tell the owner."""
    name = one_line(args.get('name'), 60)
    if not name:
        raise ValueError('Give the board a name (up to 60 characters)')
    stages, side = clean_stages(args.get('stages')), args.get('side') is not False
    about = one_line(args.get('about'), 200)
    if about is None:
        raise ValueError('Say which projects belong in one sentence (up to 200 characters)')
    board_id = int(args.get('board_id') or 0)
    old = db.execute('SELECT * FROM boards WHERE id=?', (board_id,)).fetchone() if board_id else None
    if board_id and not old:
        raise ValueError('That board is gone')
    if args.get('visibility') is not None:  # who sees it; else as it was, as suggested, or the team
        visibility = clean_visibility(args['visibility'])
    elif old:
        visibility = old['visibility'] or 'team'
    else:
        sug = db.execute('SELECT visibility FROM board_suggestions WHERE id=?', (int(args.get('suggestion_id') or 0),)).fetchone()
        visibility = (sug and sug['visibility']) or 'team'
    clash = db.execute('SELECT id FROM boards WHERE name=? COLLATE NOCASE', (name,)).fetchone()
    if clash and clash['id'] != board_id:
        raise ValueError(f'There is a board called "{name}" already')
    plain = [{k: v for k, v in s.items() if k != 'was'} for s in stages]
    notes = []
    if old:
        db.execute('UPDATE boards SET name=?,stages=?,about=?,side=?,updated_at=?,visibility=? WHERE id=?',
                   (name, json.dumps(plain), about or None, int(side), now, visibility, board_id))
        before = [s['name'] for s in json.loads(old['stages'])]
        renamed = {s['was'].casefold(): s['name'] for s in stages if s.get('was')}
        kept = [n for n in before if n.casefold() in renamed]
        for was in before:  # renamed stages carry their projects; removed ones hand them to the nearest earlier stage that stays
            if was.casefold() in renamed:
                if renamed[was.casefold()] != was:
                    db.execute('UPDATE projects SET stage=? WHERE board_id=? AND stage=? COLLATE NOCASE', (renamed[was.casefold()], board_id, was))
                continue
            earlier = [n for n in kept if before.index(n) < before.index(was)]
            to = renamed[earlier[-1].casefold()] if earlier else stages[0]['name']
            rows = db.execute('SELECT id FROM projects WHERE board_id=? AND stage=? COLLATE NOCASE', (board_id, was)).fetchall()
            for r in rows:
                db.execute('INSERT INTO project_moves(project_id,board_id,stage,reason,event_id,source,at,created_at) VALUES(?,?,?,?,?,?,?,?)',
                           (r['id'], board_id, to, f'{was} was removed', None, 'owner', now, now))
                db.execute("UPDATE projects SET stage=?,stage_reason=?,stage_event_id=NULL,stage_by=coalesce(stage_by,'owner'),stage_at=? WHERE id=?",
                           (to, f'{was} was removed', now, r['id']))
            if rows:
                notes.append({'from': was, 'to': to, 'count': len(rows)})
        if old['side'] and not side:
            gone = db.execute('UPDATE projects SET board_id=NULL,stage=NULL,stage_reason=NULL,stage_event_id=NULL,stage_by=NULL,stage_at=NULL '
                              'WHERE board_id=? AND stage=?', (board_id, STALLED)).rowcount
            if gone:
                notes.append({'from': STALLED, 'to': None, 'count': gone})
    else:
        board_id = db.execute('INSERT INTO boards(name,stages,source,created_at,about,side,updated_at,visibility) VALUES(?,?,?,?,?,?,?,?)',
                              (name, json.dumps(plain), 'owner', now, about or None, int(side), now, visibility)).lastrowid
        db.execute('INSERT OR IGNORE INTO board_backfills(board_id,started_at) VALUES(?,?)', (board_id, now))  # it looks through the history once
    names = [s['name'] for s in plain] + ([STALLED] if side else [])
    if 'projects' in args:
        wanted = open_project_rows(db, args.get('projects'))
        ids = {r['id'] for r in wanted}
        keep = f" AND id NOT IN ({','.join('?' * len(ids))})" if ids else ''
        removed = db.execute('UPDATE projects SET board_id=NULL,stage=NULL,stage_reason=NULL,stage_event_id=NULL,stage_by=NULL,stage_at=NULL,' + SORT_RESET +
                             ' WHERE board_id=?' + keep, (board_id, *ids)).rowcount
        added = 0
        for r in wanted:  # on this board now (a project is on one board); a review places it by its messages
            if r['board_id'] != board_id:
                db.execute('UPDATE projects SET board_id=?,stage=?,stage_reason=NULL,stage_event_id=NULL,stage_by=NULL,stage_at=NULL,' + SORT_RESET + ' WHERE id=?',
                           (board_id, names[0], r['id']))
                added += 1
    else:
        added = removed = 0
    if args.get('suggestion_id'):
        db.execute("UPDATE board_suggestions SET state='saved',board_id=?,decided_at=? WHERE id=?", (board_id, now, int(args['suggestion_id'])))
    return {'board_id': board_id, 'name': name, 'added': added, 'removed': removed, 'moved': notes, 'visibility': visibility}


def delete_board(db, board_id, now):
    """The board goes; its projects stay, on no board, with their history."""
    row = db.execute('SELECT * FROM boards WHERE id=?', (int(board_id or 0),)).fetchone()
    if not row:
        raise ValueError('That board is gone')
    kept = db.execute('UPDATE projects SET board_id=NULL,stage=NULL,stage_reason=NULL,stage_event_id=NULL,stage_by=NULL,stage_at=NULL '
                      'WHERE board_id=?', (row['id'],)).rowcount
    db.execute('DELETE FROM boards WHERE id=?', (row['id'],))
    db.execute('DELETE FROM board_backfills WHERE board_id=?', (row['id'],))
    db.execute('DELETE FROM board_backfill_asks WHERE board_id=?', (row['id'],))
    db.execute("UPDATE board_suggestions SET state='dismissed' WHERE board_id=?", (row['id'],))
    return {'deleted': row['name'], 'projects': kept}


def project_row(db, node_id):
    ref = str(node_id or '')
    row = db.execute('SELECT * FROM projects WHERE id=?', (ref.split(':', 1)[1],)).fetchone() if ref.startswith('project:') and ref[8:].isdigit() else None
    if not row:
        raise ValueError('That project is gone')
    return row


def save_project(db, args, now):
    """The owner's project, new or edited, like a deal in a CRM: its name, board and stage (a move by hand), organization
    (a web domain, or the name of an organization in the graph that has one), value, expected close date (YYYY-MM-DD),
    notes and the people on it (person node ids). Fields left out stay as they are."""
    node = args.get('node_id')
    if node:
        row = project_row(db, node)
        if 'name' in args and str(args['name'] or '').strip() != row['name']:
            rename(db, 'project:' + str(row['id']), args['name'], now)
    else:
        name = re.sub(r'\s+', ' ', str(args.get('name') or '')).strip()
        if not name or len(name) > 60:
            raise ValueError('Give the project a name (up to 60 characters)')
        if db.execute('SELECT 1 FROM projects WHERE name=? COLLATE NOCASE', (name,)).fetchone():
            raise ValueError(f'There is a project called "{name}" already')
        pid = db.execute("INSERT INTO projects(name,created_at,made_by) VALUES(?,?,'owner')", (name, now)).lastrowid
        row = db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
    pid, node = row['id'], 'project:' + str(row['id'])
    if 'board_id' in args:
        board_id = int(args.get('board_id') or 0)
        if not board_id:
            db.execute('UPDATE projects SET board_id=NULL,stage=NULL,stage_reason=NULL,stage_event_id=NULL,stage_by=NULL,stage_at=NULL,' + SORT_RESET +
                       ' WHERE id=?', (pid,))
        else:
            board = db.execute('SELECT * FROM boards WHERE id=?', (board_id,)).fetchone()
            if not board:
                raise ValueError('That board is gone')
            if row['board_id'] != board_id or args.get('stage'):
                set_stage(db, node, args.get('stage') or stage_names(board)[0], 'owner', now, board=board['name'])
                db.execute("DELETE FROM board_fits WHERE project_id=? AND state='open'", (pid,))
    elif args.get('stage'):
        set_stage(db, node, args['stage'], 'owner', now)
    if 'org' in args:
        said = ' '.join(str(args.get('org') or '').split())
        domain = re.sub(r'^(?:https?://)?(?:www\.)?', '', said.lower()).split('/')[0]
        if domain and not ORG_DOMAIN.fullmatch(domain):
            found = db.execute("SELECT json_extract(info,'$.domain') AS domain FROM graph_nodes WHERE kind='org' AND (id=? OR name=? COLLATE NOCASE) "
                               "AND json_extract(info,'$.domain') IS NOT NULL ORDER BY strength DESC LIMIT 1", (said, said)).fetchone()
            if not found:
                raise ValueError('Organization: its website, like "example.com", or the name of one in your Graph')
            domain = found['domain']
        db.execute('UPDATE projects SET org=? WHERE id=?', (domain or None, pid))
    if 'value' in args:
        raw = str(args.get('value') if args.get('value') is not None else '').replace(',', '').replace(' ', '').lstrip('€$£')
        try:
            value = round(float(raw), 2) if raw else None
        except ValueError:
            raise ValueError('Value: a number, like 12000') from None
        if value is not None and not 0 <= value < 1e12:
            raise ValueError('Value: a number, like 12000')
        db.execute('UPDATE projects SET value=? WHERE id=?', (value, pid))
    if 'close_date' in args:
        close = str(args.get('close_date') or '').strip()
        try:
            close = dt.date.fromisoformat(close).isoformat() if close else None
        except ValueError:
            raise ValueError('Expected close: a date, like 2026-12-15') from None
        db.execute('UPDATE projects SET close_date=? WHERE id=?', (close, pid))
    if 'notes' in args:
        notes = str(args.get('notes') or '').strip()
        if len(notes) > 4000:
            raise ValueError('Notes fit in 4000 characters')
        db.execute('UPDATE projects SET notes=? WHERE id=?', (notes or None, pid))
    if 'people' in args:
        wanted = [str(x) for x in args.get('people') or [] if str(x).strip()]
        known = {r[0] for r in db.execute(f"SELECT id FROM graph_nodes WHERE kind='person' AND id IN ({','.join('?' * len(wanted))})", wanted)} if wanted else set()
        if set(wanted) - known:
            raise ValueError('Pick people from the list')
        db.execute('DELETE FROM project_people WHERE project_id=?', (pid,))
        db.executemany('INSERT OR IGNORE INTO project_people(project_id,node_id,created_at) VALUES(?,?,?)', [(pid, n, now) for n in wanted])
        if wanted:  # the people the owner took off stay off until the messages link them again
            db.execute(f"DELETE FROM graph_links WHERE b=? AND kind='project' AND a NOT IN ({','.join('?' * len(wanted))})", (node, *wanted))
    return {'node_id': node, 'name': db.execute('SELECT name FROM projects WHERE id=?', (pid,)).fetchone()['name'], 'created': not args.get('node_id')}


def set_outcome(db, node_id, outcome, reason, now):
    """Won or lost, by the owner, with why: won goes to the board's last stage, lost to its Stalled or declined row.
    Like any move by hand it stands until a newer message moves the project. None reopens it."""
    row = project_row(db, node_id)
    reason = one_line(reason, 160)
    if reason is None:
        raise ValueError('Why, in one short line (up to 160 characters)')
    if outcome not in ('won', 'lost', None):
        raise ValueError('outcome is won or lost')
    board = db.execute('SELECT * FROM boards WHERE id=?', (row['board_id'],)).fetchone() if row['board_id'] else None
    stage = None
    if board and outcome:
        main = [s['name'] for s in json.loads(board['stages'])]
        stage = main[-1] if outcome == 'won' else STALLED if board['side'] else None
        if stage:
            set_stage(db, 'project:' + str(row['id']), stage, 'owner', now)
            db.execute('UPDATE projects SET stage_reason=? WHERE id=?', (reason or None, row['id']))
            db.execute('UPDATE project_moves SET reason=? WHERE id=(SELECT max(id) FROM project_moves WHERE project_id=?)', (reason or None, row['id']))
    db.execute('UPDATE projects SET outcome=?,outcome_reason=? WHERE id=?', (outcome, (reason or None) if outcome else None, row['id']))
    return {'node_id': 'project:' + str(row['id']), 'outcome': outcome, 'stage': stage or row['stage']}


def archive_project(db, node_id, archived, now):
    """Archived: off the boards and lists of open work, with its history; a new message about it brings it back."""
    row = project_row(db, node_id)
    db.execute('UPDATE projects SET closed_at=? WHERE id=?', (now if archived else None, row['id']))
    return {'node_id': 'project:' + str(row['id']), 'archived': bool(archived)}


def delete_project(db, node_id):
    """Gone for good: the project, its moves, labels and links. Its messages and people stay."""
    row = project_row(db, node_id)
    node = 'project:' + str(row['id'])
    for sql in ('DELETE FROM event_projects WHERE project_id=?', 'DELETE FROM project_moves WHERE project_id=?', 'DELETE FROM project_people WHERE project_id=?',
                'DELETE FROM board_fits WHERE project_id=?', 'DELETE FROM projects WHERE id=?'):
        db.execute(sql, (row['id'],))
    for sql in ('DELETE FROM graph_links WHERE a=? OR b=?', 'DELETE FROM graph_events WHERE node_id=?', 'DELETE FROM graph_nodes WHERE id=?'):
        db.execute(sql, (node, node)[:sql.count('?')])
    return {'deleted': row['name']}


def default_board(db, board_id):
    """The board Projects opens on, on every device."""
    if not db.execute('SELECT 1 FROM boards WHERE id=?', (int(board_id or 0),)).fetchone():
        raise ValueError('That board is gone')
    db.execute('UPDATE boards SET is_default=(id=?)', (int(board_id),))
    return {'default': int(board_id)}


def project_card(db, node_id):
    """What the project dialog edits: name, board, stage, organization, value, notes, people and how it ended."""
    row = project_row(db, node_id)
    people = [{'id': r['id'], 'name': r['name']} for r in db.execute(
        "SELECT n.id,n.name FROM graph_nodes n WHERE n.id IN (SELECT node_id FROM project_people WHERE project_id=? UNION "
        "SELECT a FROM graph_links WHERE b=? AND kind='project') AND n.kind='person' ORDER BY n.strength DESC LIMIT 30", (row['id'], 'project:' + str(row['id'])))]
    return {'node_id': 'project:' + str(row['id']), 'name': row['name'], 'board_id': row['board_id'], 'stage': row['stage'], 'org': row['org'],
            'value': row['value'], 'close_date': row['close_date'], 'notes': row['notes'], 'people': people, 'archived': bool(row['closed_at']), 'outcome': row['outcome'],
            'outcome_reason': row['outcome_reason']}


def fit_brief(db, run_id, now, limit=FIT_BATCH):
    """The "Projects that may fit a board" block for review run_id: open projects on no board that weren't checked
    against every board with an about yet. Marks them as given to this run (board_fits with fit 'asked')."""
    boards_ = db.execute("SELECT * FROM boards WHERE about IS NOT NULL AND about<>'' ORDER BY id").fetchall()
    if not boards_:
        return ''
    rows = db.execute('SELECT p.id,p.name FROM projects p WHERE p.board_id IS NULL AND p.closed_at IS NULL AND EXISTS(SELECT 1 FROM boards b '
                      "WHERE b.about IS NOT NULL AND b.about<>'' AND NOT EXISTS(SELECT 1 FROM board_fits f WHERE f.board_id=b.id AND f.project_id=p.id "
                      "AND f.fit<>'asked')) ORDER BY p.created_at DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        return ''
    for r in rows:
        for b in boards_:
            db.execute("INSERT INTO board_fits(board_id,project_id,fit,run_id,created_at) VALUES(?,?,'asked',?,?) ON CONFLICT(board_id,project_id) "
                       "DO UPDATE SET run_id=excluded.run_id WHERE fit='asked'", (b['id'], r['id'], run_id, now))
    lines = [f"Boards: " + '; '.join(f"{b['name']} ({' > '.join(stage_names(b))}): {b['about']}" for b in boards_)]
    for r in rows:
        people = [p['name'] for p in db.execute("SELECT n.name FROM graph_links l JOIN graph_nodes n ON n.id=l.a WHERE l.b=? AND l.kind='project' "
                                                "ORDER BY l.strength DESC LIMIT 2", ('project:' + str(r['id']),))]
        lines.append(f"- {r['name']} (project:{r['id']})" + (f" with {', '.join(people)}" if people else ''))
    return '\n'.join(lines)


def fits_said(db, answer, asked, check):
    """A review's finish_analysis fits, per (reference, board id): (fit, why, split). A fit names its board; one without
    a board counts only for something asked about one board in this run (asked: {reference: {board ids}}), so a maybe
    meant for one board never shows on the others."""
    names = {r['name'].casefold(): r['id'] for r in db.execute('SELECT id,name FROM boards')}
    said = {}
    for a in answer or []:
        fit, why = str(a.get('fit') or '').lower(), one_line(a.get('why'), 160)
        if check and (fit not in ('maybe', 'no') or not why):
            raise ValueError('fits: each one has fit "maybe" or "no" and why, in a few words (placing one that fits is graph project, board, stage)')
        if fit not in ('maybe', 'no') or not why:
            continue
        ref = str(a.get('project') or a.get('who') or '').strip().casefold()
        if a.get('board'):
            boards_ = [names.get(str(a.get('board')).strip().casefold())]
        else:
            boards_ = list(asked.get(ref, ())) if len(asked.get(ref, ())) == 1 else []
        for b in boards_:
            if b:
                said[(ref, b)] = (fit, why, 1 if fit == 'maybe' and a.get('split') is True else None)
    return said


def fit_answer(db, run_id, answer):
    """A review that got "Projects that may fit a board" answers for each before it finishes: placed on a board with
    graph (it fits), or finish_analysis fits [{"project", "board", "fit": "maybe" or "no", "why"}]. A maybe shows on
    the board as one the owner can add. Raises while one is left; returns how many it answered."""
    asked = db.execute("SELECT f.board_id,f.project_id,p.name,p.board_id on_board FROM board_fits f JOIN projects p ON p.id=f.project_id "
                       "WHERE f.run_id=? AND f.fit='asked'", (run_id,)).fetchall()
    if not asked:
        return 0
    if answer is not None and not (isinstance(answer, list) and all(isinstance(a, dict) for a in answer)):
        raise ValueError('fits is [{"project": "project:<id>", "board", "fit": "maybe" or "no", "why"}]')
    refs = collections.defaultdict(set)
    for r in asked:
        refs['project:' + str(r['project_id'])].add(r['board_id'])
        refs[r['name'].casefold()].add(r['board_id'])
    said = fits_said(db, answer, refs, True)
    left = []
    for r in asked:
        if r['on_board']:  # placed with graph (or by the owner meanwhile)
            db.execute("UPDATE board_fits SET fit=?,state='added' WHERE board_id=? AND project_id=?",
                       ('yes' if r['on_board'] == r['board_id'] else 'no', r['board_id'], r['project_id']))
            continue
        got = said.get(('project:' + str(r['project_id']), r['board_id'])) or said.get((r['name'].casefold(), r['board_id']))
        if got:
            db.execute('UPDATE board_fits SET fit=?,why=?,split=? WHERE board_id=? AND project_id=?', (*got, r['board_id'], r['project_id']))
        else:
            left.append(f"{r['name']} (project:{r['project_id']})")
    if left:
        raise ValueError('Projects that may fit a board: place ' + ', '.join(dict.fromkeys(left)) + ' with graph (project, board, stage, event_id, reason) '
                         'when it clearly fits, or add it to finish_analysis fits [{"project", "board", "fit": "maybe" or "no", "why"}], one per board it was '
                         'asked about, each naming its board')
    return len(asked)


def fit_due(db, now):
    """A board saved in the last FIT_FRESH_MINUTES with projects on no board not yet checked against it: start a review now."""
    since = (parse_time(now) - dt.timedelta(minutes=FIT_FRESH_MINUTES)).isoformat()
    return bool(db.execute("SELECT 1 FROM boards b WHERE b.about IS NOT NULL AND b.about<>'' AND coalesce(b.updated_at,b.created_at)>=? AND EXISTS("
                           "SELECT 1 FROM projects p WHERE p.board_id IS NULL AND p.closed_at IS NULL AND NOT EXISTS(SELECT 1 FROM board_fits f "
                           "WHERE f.board_id=b.id AND f.project_id=p.id AND f.fit<>'asked'))", (since,)).fetchone())


def board_maybes(db, board_id):
    """Projects a review thought could belong on this board, for the owner to add or leave out."""
    found = []
    for r in db.execute("SELECT f.project_id,f.why,f.split,p.name FROM board_fits f JOIN projects p ON p.id=f.project_id WHERE f.board_id=? AND f.fit='maybe' "
                        "AND f.state='open' AND p.board_id IS NULL AND p.closed_at IS NULL ORDER BY f.created_at DESC LIMIT 100", (board_id,)):
        found.append({'id': 'project:' + str(r['project_id']), 'name': r['name'], 'why': r['why']})
        parts = len(split_plan(db, r['project_id'])) if r['split'] else 0
        if parts >= 2:  # Add makes one project per counterpart
            found[-1]['split'] = parts
    # and organizations or people from the look through the history with no project yet (Add makes one)
    return found + [{'id': r['key'], 'name': r['name'], 'why': r['why']} for r in db.execute(
        "SELECT a.key,a.name,a.why FROM board_backfill_asks a WHERE a.board_id=? AND a.fit='maybe' AND a.state='open' AND (a.project_id IS NULL OR "
        "EXISTS(SELECT 1 FROM projects p WHERE p.id=a.project_id AND p.board_id IS NULL AND p.closed_at IS NULL)) AND NOT EXISTS(SELECT 1 FROM json_each(a.event_ids) j "
        "JOIN event_projects ep ON ep.event_id=j.value JOIN projects p ON p.id=ep.project_id WHERE p.board_id IS NOT NULL OR p.id IS NOT a.project_id) "
        'ORDER BY a.created_at DESC LIMIT 100', (board_id,))]


UNDO_COLUMNS = ('board_id', 'org', 'stage', 'stage_event_id', 'stage_by', 'stage_at', 'sort_run', 'sort_tries', 'closed_at')  # no message text: steps are stored unsealed


def counterpart_keys(db, event_ids):
    """Who each message is with, outside the owner's team: {event_id: [key]} with the organization's node id as key, or
    the person's when they have none (as the look through the history keys them)."""
    out = collections.defaultdict(list)
    ids = [int(i) for i in event_ids]
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in db.execute("SELECT g.event_id,n.id person,json_extract(n.info,'$.org') org FROM graph_events g JOIN graph_nodes n ON n.id=g.node_id "
                            f"WHERE g.event_id IN ({','.join('?' * len(part))}) AND n.kind='person' AND g.probable=0 "
                            "AND json_extract(n.info,'$.colleague') IS NOT 1 ORDER BY g.event_id,n.id", part):
            key = r['org'] if str(r['org'] or '').startswith('org:') else r['person']
            if key not in out[r['event_id']]:
                out[r['event_id']].append(key)
    return out


def split_plan(db, project_id):
    """How Add splits a project that bundles several counterparts (a review said so): one part per organization (or
    person without one) its messages are with, each going into the open project already with them when there is one
    (no duplicates), else a new "<project>: <counterpart>". [{name, key, event_ids, project_id or None}]; fewer than
    two parts means it is one project after all."""
    row = db.execute('SELECT * FROM projects WHERE id=?', (int(project_id),)).fetchone()
    ids = [r[0] for r in db.execute('SELECT ep.event_id FROM event_projects ep JOIN events e ON e.id=ep.event_id WHERE ep.project_id=? AND e.deleted=0 '
                                    'ORDER BY e.occurred_at,e.id', (row['id'],))]
    keys = counterpart_keys(db, ids)
    count = collections.Counter(k for e in ids for k in keys.get(e, ()))
    parts = {}
    for e in ids:
        if keys.get(e):
            key = max(keys[e], key=lambda k: (count[k], k))  # a message with two counterparts goes with the one most of the project is with
            parts.setdefault(key, []).append(e)
    if len(parts) < 2:
        return []
    names = {r['id']: r['name'] for r in db.execute(f"SELECT id,name FROM graph_nodes WHERE id IN ({','.join('?' * len(parts))})", list(parts))}
    owners = {}  # the open project each counterpart is with already
    for r in db.execute('SELECT id,org,board_id FROM projects WHERE closed_at IS NULL AND org IS NOT NULL AND id<>? ORDER BY board_id IS NULL,id', (row['id'],)):
        owners.setdefault('org:' + r['org'], r['id'])
    for r in db.execute("SELECT l.a,json_extract(n.info,'$.org') org,p.id FROM graph_links l JOIN graph_nodes n ON n.id=l.a JOIN projects p ON 'project:'||p.id=l.b "
                        "WHERE l.kind='project' AND p.closed_at IS NULL AND p.id<>? AND n.kind='person' ORDER BY p.board_id IS NULL,p.id", (row['id'],)):
        owners.setdefault(r['a'], r['id'])
        if str(r['org'] or '').startswith('org:'):
            owners.setdefault(r['org'], r['id'])
    out = []
    for key, events in sorted(parts.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        who = one_line(names.get(key), 60) or (key[4:] if key.startswith('org:') else key.split(':', 1)[-1])
        name = f"{row['name'][:max(10, 58 - len(who))]}: {who}"[:60].strip()
        out.append({'name': name, 'key': key, 'event_ids': events, 'project_id': owners.get(key)})
    return out


def _remember(db, steps, project_id):
    if not any(s[0] == 'project' and s[1] == project_id for s in steps):
        row = db.execute(f"SELECT {','.join(UNDO_COLUMNS)} FROM projects WHERE id=?", (project_id,)).fetchone()
        steps.append(('project', project_id, dict(row)))


def _attach(db, target, event_ids, now, steps):
    """Label messages into a project (by id, or by name: made when new); remembers what to take back."""
    if isinstance(target, int):
        pid = target
    else:
        before = db.execute('SELECT id FROM projects WHERE name=? COLLATE NOCASE', (target,)).fetchone()
        if before:
            _remember(db, steps, before['id'])
        pid = project_id(db, target, now)
        if not before:
            steps.append(('made', pid))
    have = {r[0] for r in db.execute('SELECT event_id FROM event_projects WHERE project_id=?', (pid,))}
    new = [int(e) for e in event_ids if int(e) not in have]
    db.executemany('INSERT OR IGNORE INTO event_projects(event_id,project_id,source,created_at) VALUES(?,?,?,?)', [(e, pid, 'owner', now) for e in new])
    if new and not any(s == ('made', pid) for s in steps):
        steps.append(('labels', pid, new))
    return pid


def _place(db, board, pid, steps, strict):
    """Onto the board's first stage, sorted from its messages by the next review. A project on another board stays
    there (strict: refuse)."""
    row = db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
    if row['board_id'] == board['id'] and not row['closed_at']:
        return False
    if row['board_id'] and row['board_id'] != board['id'] and not row['closed_at']:
        if strict:
            raise ValueError('It is on another board already')
        return False
    _remember(db, steps, pid)
    db.execute('UPDATE projects SET board_id=?,stage=?,stage_reason=NULL,stage_event_id=NULL,stage_by=NULL,stage_at=NULL,closed_at=NULL,' + SORT_RESET + ' WHERE id=?',
               (board['id'], stage_names(board)[0], pid))
    return True


def _split(db, board, row, now, steps):
    """Add on a project that bundles several counterparts: one project per counterpart on the board; the bundle closes
    once none of its messages is left over."""
    plan = split_plan(db, row['id'])
    if not plan:
        return None
    moved = {e for part in plan for e in part['event_ids']}
    gone = [tuple(r) for r in db.execute(f"SELECT event_id,project_id,source,created_at FROM event_projects WHERE project_id=? AND event_id IN ({','.join('?' * len(moved))})",
                                         (row['id'], *moved))]
    db.execute(f"DELETE FROM event_projects WHERE project_id=? AND event_id IN ({','.join('?' * len(moved))})", (row['id'], *moved))
    steps.append(('unlabels', gone))
    placed = 0
    for part in plan:
        pid = _attach(db, part['project_id'] or part['name'], part['event_ids'], now, steps)
        if part['key'].startswith('org:') and not part['key'].startswith('org:named:'):
            if not db.execute('SELECT org FROM projects WHERE id=?', (pid,)).fetchone()['org']:
                _remember(db, steps, pid)
                db.execute('UPDATE projects SET org=? WHERE id=?', (part['key'][4:], pid))
        placed += _place(db, board, pid, steps, strict=False) or db.execute('SELECT board_id FROM projects WHERE id=?', (pid,)).fetchone()[0] == board['id']
    if not db.execute('SELECT 1 FROM event_projects WHERE project_id=?', (row['id'],)).fetchone():
        _remember(db, steps, row['id'])
        db.execute('UPDATE projects SET closed_at=? WHERE id=?', (now, row['id']))
    return placed


def _decide(db, board, node_id, add, now, steps, strict=True):
    node_id = str(node_id or '')
    if not node_id.startswith('project:'):
        ask = db.execute("SELECT * FROM board_backfill_asks WHERE board_id=? AND key=? AND fit='maybe'", (board['id'], node_id)).fetchone()
        if not ask:
            raise ValueError('That suggestion is gone')
        steps.append(('ask', board['id'], ask['key'], ask['state'], ask['project_id'], ask['decided_at']))
        pid = ask['project_id']
        if add and not pid:
            same = db.execute('SELECT board_id FROM projects WHERE name=? COLLATE NOCASE AND closed_at IS NULL', (ask['name'],)).fetchone()
            if same and same['board_id'] and same['board_id'] != board['id']:
                raise ValueError('A project by that name is on another board already')
            ids = [r[0] for r in db.execute('SELECT e.id FROM events e JOIN json_each(?) j ON j.value=e.id WHERE e.deleted=0', (ask['event_ids'],))]
            pid = _attach(db, ask['name'], ids, now, steps)
            if ask['key'].startswith('org:') and not ask['key'].startswith('org:named:'):
                db.execute('UPDATE projects SET org=coalesce(org,?) WHERE id=?', (ask['key'][4:], pid))
        db.execute('UPDATE board_backfill_asks SET state=?,decided_at=?,project_id=? WHERE board_id=? AND key=?',
                   ('added' if add else 'dismissed', now, pid, board['id'], ask['key']))
        if not add:
            return 0, None
        node_id = 'project:' + str(pid)
    row = project_row(db, node_id)
    fit = db.execute('SELECT * FROM board_fits WHERE board_id=? AND project_id=?', (board['id'], row['id'])).fetchone()
    if fit:
        steps.append(('fit', board['id'], row['id'], fit['state'], fit['decided_at']))
    db.execute("UPDATE board_fits SET state=?,decided_at=? WHERE board_id=? AND project_id=?", ('added' if add else 'dismissed', now, board['id'], row['id']))
    if not add:
        return 0, row['id']
    if fit and fit['split'] and not row['board_id']:
        placed = _split(db, board, row, now, steps)
        if placed is not None:
            return placed, row['id']
    return int(_place(db, board, row['id'], steps, strict) or row['board_id'] == board['id']), row['id']


def decide_fit(db, board_id, node_id, add, now):
    """The owner adds maybes to the board (sorted from their messages next) or leaves them out for good: node_id, or
    node_ids for several at once. A maybe from the look through the history (an organization or person, no project
    yet) becomes a project; a project a review said bundles several counterparts becomes one per counterpart. Returns
    how many projects it put on the board and an undo token."""
    board = db.execute('SELECT * FROM boards WHERE id=?', (int(board_id or 0),)).fetchone()
    if not board:
        raise ValueError('That board is gone')
    many = isinstance(node_id, list)
    ids = list(dict.fromkeys(str(n) for n in node_id)) if many else [node_id]
    if not ids or len(ids) > 200:
        raise ValueError('Decide 1 to 200 at once')
    steps, placed, skipped, pid = [], 0, [], None
    for n in ids:
        if not many:
            got, pid = _decide(db, board, n, add, now, steps)
            placed += got
            continue
        db.execute('SAVEPOINT fit_one')
        mark = len(steps)
        try:
            placed += _decide(db, board, n, add, now, steps, strict=False)[0]
            db.execute('RELEASE fit_one')
        except ValueError:
            db.execute('ROLLBACK TO fit_one')
            db.execute('RELEASE fit_one')
            del steps[mark:]
            skipped.append(n)
    token = secrets.token_hex(8)
    db.execute('DELETE FROM board_fit_undo WHERE created_at<?', ((parse_time(now) - dt.timedelta(days=1)).isoformat(),))
    db.execute('INSERT INTO board_fit_undo(token,board_id,steps,created_at) VALUES(?,?,?,?)', (token, board['id'], json.dumps(steps), now))
    out = {'added': bool(add), 'decided': len(ids) - len(skipped), 'projects': placed, 'skipped': len(skipped), 'undo': token}
    if not many:
        out['node_id'] = 'project:' + str(pid) if pid else str(node_id)
    return out


def undo_fit(db, token):
    """Take back one Add or Leave out (one or several at once) while its toast shows: the projects go back where they
    were, made ones go, split messages return to the bundle and the suggestions are open again."""
    row = db.execute('SELECT * FROM board_fit_undo WHERE token=?', (str(token or ''),)).fetchone()
    if not row:
        raise ValueError('Too late to undo that')
    steps = json.loads(row['steps'])
    for step in reversed(steps):
        kind = step[0]
        if kind == 'made':
            if db.execute('SELECT 1 FROM projects WHERE id=?', (step[1],)).fetchone():
                delete_project(db, 'project:' + str(step[1]))
        elif kind == 'labels':
            db.execute(f"DELETE FROM event_projects WHERE project_id=? AND event_id IN ({','.join('?' * len(step[2]))})", (step[1], *step[2]))
        elif kind == 'unlabels':
            db.executemany('INSERT OR IGNORE INTO event_projects(event_id,project_id,source,created_at) VALUES(?,?,?,?)', [tuple(r) for r in step[1]])
        elif kind == 'project':
            cols = [c for c in UNDO_COLUMNS if c in step[2]]
            db.execute(f"UPDATE projects SET {','.join(c + '=?' for c in cols)} WHERE id=?", (*[step[2][c] for c in cols], step[1]))
        elif kind == 'fit':
            db.execute('UPDATE board_fits SET state=?,decided_at=? WHERE board_id=? AND project_id=?', (step[3], step[4], step[1], step[2]))
        elif kind == 'ask':
            db.execute('UPDATE board_backfill_asks SET state=?,project_id=?,decided_at=? WHERE board_id=? AND key=?', (step[3], step[4], step[5], step[1], step[2]))
    db.execute('DELETE FROM board_fit_undo WHERE token=?', (row['token'],))
    return {'undone': len(steps)}


def backfill_counterparts(db):
    """Who a new board's look through the history goes over: each organization (or person without one) the owner has
    messages or meetings with that are in no project yet, leaving out colleagues and anyone an open project is with
    already (no duplicates). {key (its graph node id): {name, people, event_ids newest first, first, last}}."""
    taken = {'org:' + r[0] for r in db.execute('SELECT org FROM projects WHERE closed_at IS NULL AND org IS NOT NULL')}
    for r in db.execute("SELECT l.a,json_extract(n.info,'$.org') org FROM graph_links l JOIN graph_nodes n ON n.id=l.a JOIN projects p ON 'project:'||p.id=l.b "
                        "WHERE l.kind='project' AND p.closed_at IS NULL AND n.kind='person' AND json_extract(n.info,'$.colleague') IS NOT 1"):
        taken.add(r['a'])
        if str(r['org'] or '').startswith('org:'):
            taken.add(r['org'])
    out, seen = {}, set()
    for r in db.execute("SELECT n.id person,n.name pname,json_extract(n.info,'$.org') org,e.id event_id,e.occurred_at FROM graph_events g "
                        "JOIN graph_nodes n ON n.id=g.node_id JOIN events e ON e.id=g.event_id "
                        f"WHERE n.kind='person' AND g.probable=0 AND e.deleted=0 AND e.kind IN ({','.join('?' * len(BACKFILL_KINDS))}) "
                        "AND json_extract(n.info,'$.colleague') IS NOT 1 AND NOT EXISTS(SELECT 1 FROM event_projects ep WHERE ep.event_id=e.id) "
                        'ORDER BY e.occurred_at DESC,e.id DESC', BACKFILL_KINDS):
        key = r['org'] if str(r['org'] or '').startswith('org:') else r['person']
        if key in taken or r['person'] in taken:
            continue
        c = out.setdefault(key, {'name': None, 'people': [], 'event_ids': [], 'first': r['occurred_at'], 'last': r['occurred_at']})
        if (key, r['event_id']) not in seen:
            seen.add((key, r['event_id']))
            c['event_ids'].append(r['event_id'])
            c['first'] = r['occurred_at']
        if r['pname'] not in c['people'] and len(c['people']) < 3:
            c['people'].append(r['pname'])
    keys = list(out)
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        for r in db.execute(f"SELECT id,name FROM graph_nodes WHERE id IN ({','.join('?' * len(part))})", part):
            out[r['id']]['name'] = r['name']
    for key, c in out.items():
        c['name'] = (one_line(c['name'], 60) or '') or (key[4:] if key.startswith('org:') else key.split(':', 1)[-1])[:60]
    return out


def backfill_pending(db):
    """Boards still looking through the history whose candidates aren't ranked yet: [(board row, its text)]."""
    return [(r, f"{r['name']}: {r['about'] or ''}") for r in db.execute(
        "SELECT b.* FROM board_backfills f JOIN boards b ON b.id=f.board_id WHERE f.state='running' AND f.ranked IS NULL ORDER BY b.id")]


def rank_backfill(db, board_id, scores=None):
    """The order a board's look goes through the counterparts: by meaning (scores, key -> higher is likelier) when the
    Machine has local embeddings, else the ones with the most messages first; at most BACKFILL_MAX."""
    found = backfill_counterparts(db)
    order = sorted(found, key=lambda k: ((scores or {}).get(k, -9), len(found[k]['event_ids']), found[k]['last']), reverse=True)
    db.execute("UPDATE board_backfills SET ranked=? WHERE board_id=? AND state='running'", (json.dumps(order[:BACKFILL_MAX]), board_id))
    return len(order[:BACKFILL_MAX])


def backfill_due(db, now=None):
    """A board is still looking through the history (and has something left to ask): reviews come sooner, while the
    model has room."""
    if now:
        backfill_settle(db, now)
    return bool(db.execute("SELECT 1 FROM board_backfills WHERE state='running' LIMIT 1").fetchone())


def backfill_settle(db, now):
    """A look is done once every counterpart it ranked has an answer (or got a project meanwhile) and every project on
    no board was checked against the board; after BACKFILL_GIVE_UP_DAYS it stops anyway."""
    found = None
    give_up = (parse_time(now) - dt.timedelta(days=BACKFILL_GIVE_UP_DAYS)).isoformat()
    for f in db.execute("SELECT f.*,b.about FROM board_backfills f JOIN boards b ON b.id=f.board_id WHERE f.state='running' AND f.ranked IS NOT NULL").fetchall():
        if f['started_at'] >= give_up:
            found = backfill_counterparts(db) if found is None else found
            answered = {r[0] for r in db.execute("SELECT key FROM board_backfill_asks WHERE board_id=? AND fit<>'asked'", (f['board_id'],))}
            if any(k in found and k not in answered for k in json.loads(f['ranked'])):
                continue
            if (f['about'] or '').strip() and db.execute(
                    "SELECT 1 FROM projects p WHERE p.board_id IS NULL AND p.closed_at IS NULL AND NOT EXISTS(SELECT 1 FROM board_fits x "
                    "WHERE x.board_id=? AND x.project_id=p.id AND x.fit<>'asked')", (f['board_id'],)).fetchone():
                continue
        db.execute("UPDATE board_backfills SET state='done',finished_at=? WHERE board_id=?", (now, f['board_id']))


def backfill_brief(db, run_id, now, describe, batch=BACKFILL_BATCH, chars=BACKFILL_CHARS):
    """The "Looking through the history for new boards" block for review run_id: the next counterparts (the likeliest
    first) with a few of their messages, checked against each board still looking. Marks them as given to this run.
    describe(event_ids) gives a line per message."""
    backfill_settle(db, now)
    running = db.execute("SELECT f.board_id,f.ranked,b.* FROM board_backfills f JOIN boards b ON b.id=f.board_id "
                         "WHERE f.state='running' AND f.ranked IS NOT NULL ORDER BY b.id").fetchall()
    if not running:
        return ''
    found = backfill_counterparts(db)
    answered = {(r[0], r[1]) for r in db.execute("SELECT board_id,key FROM board_backfill_asks WHERE fit<>'asked'")}
    rank, wants = {}, collections.defaultdict(list)
    for b in running:
        for i, key in enumerate(json.loads(b['ranked'])):
            if key in found and (b['id'], key) not in answered:
                wants[key].append(b)
                rank[key] = min(rank.get(key, i), i)
    lines, size = [], 0
    for key in sorted(wants, key=rank.get)[:batch]:
        c = found[key]
        head = (f"- {c['name']} ({key}): {len(c['event_ids'])} message{'s' if len(c['event_ids']) != 1 else ''} or meeting"
                f"{'s' if len(c['event_ids']) != 1 else ''}, {c['first'][:10]} to {c['last'][:10]}"
                + (f", with {', '.join(c['people'])}" if c['people'] else '')
                + (f"; check against {', '.join(b['name'] for b in wants[key])} only" if len(wants[key]) < len(running) else ''))
        block = head + ''.join('\n    ' + line for line in describe(c['event_ids'][:4]))
        if lines and size + len(block) > chars:
            break
        lines.append(block)
        size += len(block)
        for b in wants[key]:
            db.execute("INSERT INTO board_backfill_asks(board_id,key,name,event_ids,fit,run_id,created_at) VALUES(?,?,?,?,'asked',?,?) "
                       "ON CONFLICT(board_id,key) DO UPDATE SET run_id=excluded.run_id,name=excluded.name,event_ids=excluded.event_ids WHERE fit='asked'",
                       (b['id'], key, c['name'], json.dumps(c['event_ids'][:60]), run_id, now))
    if not lines:
        return ''
    boards_line = 'Boards: ' + '; '.join(f"{b['name']} ({' > '.join(stage_names(b))}{', only you' if b['visibility'] == 'only_you' else ''}): "
                                         f"{b['about'] or 'no description'}" for b in running)
    return boards_line + '\n' + '\n'.join(lines)


def backfill_answer(db, run_id, answer):
    """A review that got the look through the history answers for each counterpart before it finishes: made into a
    project on the board with graph (it fits), or finish_analysis fits [{"project": its key, "board", "fit": "maybe"
    or "no", "why"}]. A maybe waits on the board for Add or Leave out. Raises while one is left; returns how many."""
    asked = db.execute("SELECT * FROM board_backfill_asks WHERE run_id=? AND fit='asked'", (run_id,)).fetchall()
    if not asked:
        return 0
    if answer is not None and not (isinstance(answer, list) and all(isinstance(a, dict) for a in answer)):
        raise ValueError('fits is [{"project": "<its key>", "board", "fit": "maybe" or "no", "why"}]')
    refs = collections.defaultdict(set)
    for r in asked:
        refs[r['key'].casefold()].add(r['board_id'])
        refs[r['name'].casefold()].add(r['board_id'])
    said = fits_said(db, answer, refs, False)
    left = []
    for r in asked:
        ids = [int(i) for i in json.loads(r['event_ids'])]
        placed = db.execute(f"SELECT p.id,p.board_id FROM event_projects ep JOIN projects p ON p.id=ep.project_id WHERE ep.event_id IN ({','.join('?' * len(ids))}) "
                            'AND p.closed_at IS NULL ORDER BY p.board_id IS ? DESC LIMIT 1', (*ids, r['board_id'])).fetchone() if ids else None
        got = said.get((r['key'].casefold(), r['board_id'])) or said.get((r['name'].casefold(), r['board_id']))
        if placed and placed['board_id'] == r['board_id']:
            fit, why, state = 'yes', None, 'added'
        elif got:
            (fit, why, _), state = got, 'open'
        elif placed:  # made into a project for another board or none: that project gets its own check
            fit, why, state = 'other', None, 'open'
        else:
            left.append(f"{r['name']} ({r['key']})")
            continue
        db.execute('UPDATE board_backfill_asks SET fit=?,why=?,state=?,project_id=? WHERE board_id=? AND key=?',
                   (fit, why, state, placed['id'] if placed else None, r['board_id'], r['key']))
    if left:
        raise ValueError('Looking through the history: make ' + ', '.join(dict.fromkeys(left)) + ' a project on the board with graph (project, event_ids, '
                         'board, stage, event_id, reason) when it clearly fits, or add it to finish_analysis fits [{"project": its key, "board", '
                         '"fit": "maybe" or "no", "why"}], one per board it was asked about, each naming its board')
    return len(asked)


def backfill_view(db, board_id, now, maybes):
    """What the board header says about its look through the history: looking, or (for BACKFILL_SHOWN_DAYS after) how
    many it added and how many wait for the owner."""
    f = db.execute('SELECT * FROM board_backfills WHERE board_id=?', (board_id,)).fetchone()
    if not f:
        return None
    if f['state'] == 'running':
        return {'state': 'running'}
    if not f['finished_at'] or parse_time(f['finished_at']) < parse_time(now) - dt.timedelta(days=BACKFILL_SHOWN_DAYS):
        return None
    added = db.execute("SELECT count(*) FROM projects p WHERE p.board_id=? AND p.closed_at IS NULL AND EXISTS(SELECT 1 FROM project_moves m "
                       "WHERE m.project_id=p.id AND m.board_id=p.board_id AND m.source<>'owner' AND m.created_at>=?) AND NOT EXISTS("
                       "SELECT 1 FROM project_moves m WHERE m.project_id=p.id AND m.board_id=p.board_id AND m.created_at<?)",
                       (board_id, f['started_at'], f['started_at'])).fetchone()[0]
    added += db.execute("SELECT count(*) FROM board_backfill_asks a JOIN projects p ON p.id=a.project_id WHERE a.board_id=? AND a.fit='maybe' "
                        "AND a.state='added' AND p.board_id=? AND p.stage_by IS NULL", (board_id, board_id)).fetchone()[0]
    return {'state': 'done', 'added': added, 'review': len(maybes)}


MESSAGE_KINDS = ('gmail.message', 'salesflare.email', 'linkedin.message', 'slack.message', 'whatsapp.message', 'intercom.conversation', 'x.dm',
                 'ms_graph.message', 'outlook.message')


def org_card(db, domain):
    """An organization's name and logo for a tile: the owner's edits over the enrichment service's profile."""
    found = db.execute("SELECT name,info FROM graph_nodes WHERE id=?", ('org:' + domain,)).fetchone()
    info = json.loads(found['info'] or '{}') if found else {}
    profile = info.get('profile')
    if profile is None:
        row = db.execute("SELECT profile FROM org_profiles WHERE domain=? AND status='found'", (domain,)).fetchone()
        edit = db.execute('SELECT fields FROM org_edits WHERE domain=?', (domain,)).fetchone()
        profile = {**(json.loads(row['profile'] or '{}') if row else {}), **(json.loads(edit['fields'] or '{}') if edit else {})}
    if info.get('wrong'):
        profile = {}
    logo = (profile or {}).get('logo')
    return {'node_id': 'org:' + domain if found else None, 'name': found['name'] if found else (profile or {}).get('name') or domain,
            'logo': logo if isinstance(logo, str) and logo.startswith('https://') else None}


def boards(db, now):
    """Every board with its stages and projects, for the Projects board view. Each project: its stage, why it last
    moved and when (with the message), its next step, its organization (logo from enrichment), how long it has been
    in the stage, how long the other side hasn't answered the owner (no_reply_days, from its messages), and an open
    card about it."""
    out = []
    today = parse_time(now)
    for b in db.execute('SELECT * FROM boards ORDER BY is_default DESC,id').fetchall():
        rows = db.execute('SELECT * FROM projects WHERE board_id=? AND closed_at IS NULL ORDER BY coalesce(stage_at,created_at) DESC', (b['id'],)).fetchall()
        names, items = stage_names(b), []
        for p in rows:
            node = 'project:' + str(p['id'])
            domain = p['org']
            if not domain:  # the organization most of its people are at, leaving out the owner's colleagues
                weight = collections.Counter()
                for r in db.execute("SELECT json_extract(n.info,'$.org') org,l.strength FROM graph_links l JOIN graph_nodes n ON n.id=l.a "
                                    "WHERE l.b=? AND l.kind='project' AND n.kind='person' AND json_extract(n.info,'$.colleague') IS NOT 1", (node,)):
                    if r['org'] and str(r['org']).startswith('org:') and not str(r['org']).startswith('org:named:'):
                        weight[r['org'][4:]] += r['strength']
                domain = weight.most_common(1)[0][0] if weight else None
            mine_since = None  # the first of the owner's messages since the other side last wrote
            for r in db.execute("SELECT e.occurred_at,(json_extract(e.metadata,'$.from_me') IS 1 OR EXISTS(SELECT 1 FROM json_each(e.metadata,'$.labels') "
                                "WHERE value='SENT')) mine FROM graph_events g JOIN events e ON e.id=g.event_id WHERE g.node_id=? AND g.probable=0 "
                                f"AND e.deleted=0 AND e.kind IN ({','.join('?' for _ in MESSAGE_KINDS)}) ORDER BY e.occurred_at", (node, *MESSAGE_KINDS)):
                mine_since = (mine_since or r['occurred_at']) if r['mine'] else None
            since = parse_time(p['stage_at'] or p['created_at'])
            last = None if p['stage_by'] else next(iter(project_events(db, p['id'], 1, now)), None)  # an unplaced tile: its latest message
            card = db.execute(f"SELECT s.id,s.title FROM suggestions s WHERE s.status IN ({','.join('?' for _ in OPEN_CARD)}) AND (s.project=? COLLATE NOCASE "
                              "OR EXISTS(SELECT 1 FROM json_each(s.event_ids) j JOIN graph_events g ON g.event_id=j.value WHERE g.node_id=? AND g.probable=0)) "
                              "ORDER BY s.created_at DESC LIMIT 1", (*OPEN_CARD, p['name'], node)).fetchone()
            items.append({'id': node, 'name': p['name'], 'stage': p['stage'] if p['stage'] in names else names[0], 'reason': p['stage_reason'],
                          'by': p['stage_by'], **sort_view(db, p), 'at': p['stage_at'], 'event_id': p['stage_event_id'], 'next': p['next_step'],
                          'first': not db.execute('SELECT 1 FROM project_moves WHERE project_id=? LIMIT 1 OFFSET 1', (p['id'],)).fetchone(),
                          'days': max(0, (today - since).days), 'closed': bool(p['closed_at']),
                          'no_reply_days': max(0, (today - parse_time(mine_since)).days) if mine_since else None,
                          'org': org_card(db, domain) if domain else None, 'card': {'id': card['id'], 'title': card['title']} if card else None,
                          **({'last_at': last['occurred_at'], 'last_event_id': last['id']} if last else {}),
                          **({'value': p['value']} if p['value'] is not None else {}), **({'close_date': p['close_date']} if p['close_date'] else {}),
                          **({'outcome': p['outcome']} if p['outcome'] else {})})
        out.append({'id': b['id'], 'name': b['name'], 'about': b['about'], 'side': bool(b['side']), 'visibility': b['visibility'] or 'team',
                    'stages': json.loads(b['stages']) + ([{'name': STALLED, 'side': True}] if b['side'] else []), 'projects': items,
                    'stuck': sum(1 for i in items if (i['no_reply_days'] or 0) >= STUCK_DAYS and i['stage'] != STALLED),
                    'sorting': sum(1 for i in items if i['sorting']), 'default': bool(b['is_default']), 'maybes': board_maybes(db, b['id'])})
        out[-1]['backfill'] = backfill_view(db, b['id'], now, out[-1]['maybes'])
    return out


def project_moves(db, node_id, limit=8):
    """A project's board, stage and its latest moves (newest first), for its page."""
    row = db.execute('SELECT p.*,b.name board,b.visibility board_visibility FROM projects p LEFT JOIN boards b ON b.id=p.board_id WHERE p.id=?',
                     (str(node_id).split(':', 1)[1],)).fetchone()
    if not row or not row['board_id']:
        return None
    moves = [dict(r) for r in db.execute('SELECT stage,reason,event_id,source,at FROM project_moves WHERE project_id=? ORDER BY at DESC,id DESC LIMIT ?',
                                         (row['id'], limit))]
    return {'board': row['board'], 'stage': row['stage'], 'next': row['next_step'], 'moves': moves, 'visibility': row['visibility'],
            'board_visibility': row['board_visibility'] or 'team',
            'stages': stage_names(db.execute('SELECT * FROM boards WHERE id=?', (row['board_id'],)).fetchone())}


def merge(db, a, b, same, now, source='agent'):
    """Merge (same) or Not the same. Not the same on a pair that was joined undoes that join for good: the two are never
    joined again, directly or through others, until someone merges them."""
    a, b = sorted((str(a), str(b)))
    if a == b or not all(re.fullmatch(r'(?:email|wa|slack|li|x|gh):\S+', i) for i in (a, b)):
        raise ValueError('Merge two different person ids from graph results (like email:… or wa:…)')
    gone = forgotten(db)
    if gone(a) or gone(b):
        raise ValueError('That person was deleted permanently')
    said = db.execute('SELECT source FROM graph_merges WHERE a=? AND b=?', (a, b)).fetchone()
    if said and said['source'] == 'owner' and source != 'owner':
        raise ValueError('The owner already decided about these two; their word stays')
    db.execute('INSERT OR REPLACE INTO graph_merges(a,b,same,created_at,source) VALUES(?,?,?,?,?)', (a, b, int(bool(same)), now, source))
    db.execute('DELETE FROM graph_same WHERE a=? AND b=?', (a, b))


def merge_all(db, pairs, now):
    """Merge all: the owner merges every listed Same person? pair at once. Only pairs still asked count, so a pair marked
    Not the same (or deleted, or merged meanwhile) is never in it. The batch is its time stamp, for one Undo."""
    asked = {(r['a'], r['b']) for r in db.execute('SELECT a,b FROM graph_same')}
    done = 0
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or tuple(sorted(map(str, pair))) not in asked:
            continue
        a, b = sorted(map(str, pair))
        if db.execute('SELECT 1 FROM graph_merges WHERE a=? AND b=?', (a, b)).fetchone():
            continue  # someone already decided about these two
        merge(db, a, b, True, now, 'owner')
        done += 1
    return {'merged': done, 'batch': now if done else None}


def unmerge_all(db, batch):
    """Undo Merge all: the batch's merges go, so the pairs are separate people and asked again; any other decision stays."""
    return db.execute("DELETE FROM graph_merges WHERE created_at=? AND same=1 AND source='owner'", (str(batch),)).rowcount


# Delete permanently (the owner's right to have someone forgotten, GDPR art. 17): the person and what is tied to them
# go; what stays is a keyed hash of each of their identifiers, so they are never added, looked up or learned again.
class _Everyone:
    def __contains__(self, item):
        return True


def _salt(db, create=False):
    row = db.execute('SELECT salt FROM forgotten_salt WHERE id=1').fetchone()
    if row or not create:
        return row[0] if row else None
    salt = secrets.token_hex(32)
    db.execute('INSERT INTO forgotten_salt(id,salt) VALUES(1,?)', (salt,))
    return salt


def forgotten_digest(salt, identifier):
    return hmac.new(bytes.fromhex(salt), ' '.join(str(identifier).split()).casefold().encode(), hashlib.sha256).hexdigest()


def anyone_forgotten(db):
    try:
        return bool(db.execute('SELECT 1 FROM forgotten_people LIMIT 1').fetchone())
    except sqlite3.OperationalError:
        return False


def forgotten(db):
    """A check, identifier -> True when that person was deleted permanently. Cheap when nobody was."""
    try:
        digests = {r[0] for r in db.execute('SELECT digest FROM forgotten_people')}
        salt = _salt(db) if digests else None
    except sqlite3.OperationalError:
        digests, salt = set(), None
    if not digests or not salt:
        return lambda identifier: False
    return lambda identifier: forgotten_digest(salt, identifier) in digests


def event_parties(kind, m, owner):
    """(sender, others) of one signal as graph identifiers: who wrote it (None when the owner did or for a meeting)
    and everyone else in it but the owner."""
    found = touches(kind, m, owner, _Everyone())
    roles = {role for _, _, _, role in found}
    sender = None
    if found and not roles & {'owner', 'met'} and kind not in ('calendar.event', 'salesflare.meeting', 'salesflare.email', 'owner.card'):
        sender = found[0][0]
    return sender, {t[0] for t in found}


def from_forgotten(kind, m, owner, gone):
    """True when a signal is from a deleted person, or they are the only other one in it."""
    sender, others = event_parties(kind, m, owner)
    return bool((sender and gone(sender)) or (others and all(gone(i) for i in others)))


def _table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _scrub(text, needles):
    """Their names and addresses out of a text the owner keeps (a card, a draft, a promise)."""
    if not isinstance(text, str) or not text:
        return text
    for needle in sorted(needles, key=len, reverse=True):
        text = re.sub(r'(?<![\w@.])' + re.escape(needle) + r'(?![\w@])', 'someone', text, flags=re.IGNORECASE)
    return text


def mentions(text, needles):
    return isinstance(text, str) and any(re.search(r'(?<![\w@.])' + re.escape(n) + r'(?![\w@])', text, re.IGNORECASE) for n in needles)


def delete_person(db, node_id, config, now):
    """Deletes a person permanently: their graph rows, links, aliases and addresses, looked-up details and photo link,
    relationship scores, the messages they sent or that were only between them and the owner, the notes learned about
    them. Cards that involve them stay, as the owner's own work, with their name and addresses taken out. Returns
    {'identifiers', 'events', 'facts', 'cards', 'needles'}; the caller rebuilds the graph."""
    row = db.execute("SELECT id,name,info FROM graph_nodes WHERE id=? AND kind='person'", (str(node_id or ''),)).fetchone()
    if not row:
        raise ValueError('Unknown person')
    info = json.loads(row['info'] or '{}')
    ids = sorted({str(i) for i in info.get('identifiers') or ()} | {row['id']})
    for r in db.execute('SELECT a,b FROM graph_merges WHERE same=1').fetchall():  # aliases joined by hand
        if r['a'] in ids or r['b'] in ids:
            ids = sorted(set(ids) | {r['a'], r['b']})
    emails = sorted({i.split(':', 1)[1].lower() for i in ids if i.startswith('email:')})
    if emails and _table(db, 'identity_names'):  # their other channels, known by their address
        ids = sorted(set(ids) | {r[0] for r in db.execute('SELECT identifier FROM identity_names WHERE lower(email) IN (%s)' % ','.join('?' * len(emails)), emails)})
    names = {' '.join(str(row['name']).split())}
    names |= {r[0] for r in db.execute('SELECT name FROM graph_names WHERE node_id IN (%s)' % ','.join('?' * len(ids)), ids)}
    names |= {r[0] for r in db.execute('SELECT name FROM identity_names WHERE name IS NOT NULL AND identifier IN (%s)' % ','.join('?' * len(ids)), ids)}
    full = {n for n in names if n and plain_name(n)}
    needles = sorted(full | set(emails))
    salt = _salt(db, create=True)
    db.executemany('INSERT OR IGNORE INTO forgotten_people(digest,created_at) VALUES(?,?)', [(forgotten_digest(salt, i), now) for i in ids])
    gone = forgotten(db)
    owner = Owner(config or {})
    # Messages they sent, or only between them and the owner.
    dropped = []
    if _table(db, 'events'):
        for e in db.execute('SELECT id,kind,metadata FROM events').fetchall():
            try:
                m = json.loads(e['metadata'])
            except (TypeError, ValueError):
                continue
            if isinstance(m, dict) and from_forgotten(e['kind'], m, owner, gone):
                dropped.append(e['id'])
    marks = ','.join('?' * len(ids))
    for table, column in (('event_vectors', 'event_id'), ('event_projects', 'event_id'), ('graph_events', 'event_id'), ('events', 'id')):
        if _table(db, table):
            db.executemany(f'DELETE FROM {table} WHERE {column}=?', [(i,) for i in dropped])
    if dropped and _table(db, 'analyses'):
        gone_events = set(dropped)
        for review in db.execute('SELECT id,items FROM analyses').fetchall():
            items = json.loads(review['items'] or '[]')
            if any(isinstance(item, dict) and item.get('id') in gone_events for item in items):
                for item in items:
                    if isinstance(item, dict) and item.get('id') in gone_events:
                        item['excluded'] = 'person_deleted'
                db.execute('UPDATE analyses SET items=? WHERE id=?', (json.dumps(items), review['id']))
    # The graph: them, their links and scores, aliases, roles, where they work, relations, looked-up details.
    db.execute(f'DELETE FROM graph_nodes WHERE id IN ({marks})', ids)
    db.execute(f'DELETE FROM graph_links WHERE a IN ({marks}) OR b IN ({marks})', ids + ids)
    db.execute(f'DELETE FROM graph_events WHERE node_id IN ({marks})', ids)
    db.execute(f'DELETE FROM graph_merges WHERE a IN ({marks}) OR b IN ({marks})', ids + ids)
    if _table(db, 'graph_same'):
        db.execute(f'DELETE FROM graph_same WHERE a IN ({marks}) OR b IN ({marks})', ids + ids)
    db.execute(f'DELETE FROM graph_relations WHERE a IN ({marks}) OR b IN ({marks})', ids + ids)
    for table in ('graph_names',):
        db.execute(f'DELETE FROM {table} WHERE node_id IN ({marks})', ids)
    for table in ('identity_names', 'manual_nodes', 'person_roles', 'person_orgs', 'person_edits', 'graph_role_asks', 'graph_org_asks', 'person_profiles', 'person_photos', 'person_photo_tries', 'contact_photo_links', 'whatsapp_photo_asks', 'person_facts', 'role_corrections'):
        if _table(db, table):
            db.execute(f'DELETE FROM {table} WHERE identifier IN ({marks})', ids)
    if emails:
        db.executemany('DELETE FROM identity_names WHERE lower(email)=?', [(e,) for e in emails])
    if _table(db, 'network_people'):
        db.execute(f'DELETE FROM network_people WHERE node_id IN ({marks})', ids)
        db.executemany('DELETE FROM network_people WHERE lower(email)=?', [(e,) for e in emails])
    # Learned notes about them, or that name them.
    facts = set()
    if _table(db, 'fact_about'):
        facts |= {r[0] for r in db.execute(f'SELECT fact_id FROM fact_about WHERE node_id IN ({marks})', ids)}
    if _table(db, 'facts'):
        facts |= {r['id'] for r in db.execute('SELECT id,text FROM facts').fetchall() if mentions(r['text'], needles)}
        for table, column in (('fact_about', 'fact_id'), ('fact_vectors', 'fact_id'), ('facts', 'id')):
            if _table(db, table):
                db.executemany(f'DELETE FROM {table} WHERE {column}=?', [(f,) for f in facts])
    # Cards stay as the owner's work, without their name or addresses.
    cards = 0
    if _table(db, 'suggestions'):
        dropped_set = set(dropped)
        for card in db.execute('SELECT id,title,body,event_ids FROM suggestions').fetchall():
            try:
                linked = bool(dropped_set & set(json.loads(card['event_ids'] or '[]')))
            except (TypeError, ValueError):
                linked = False
            if not (linked or mentions(card['title'], needles) or mentions(card['body'], needles)):
                continue
            cards += 1
            first = {n.split()[0] for n in full if len(n.split()[0]) > 1}  # on a card about them, "Hi Lee" too
            card_needles = needles + sorted(first)
            db.execute('UPDATE suggestions SET title=?,body=? WHERE id=?', (_scrub(card['title'], card_needles), _scrub(card['body'], card_needles), card['id']))
            if _table(db, 'decisions'):
                for d in db.execute('SELECT id,feedback FROM decisions WHERE suggestion_id=?', (card['id'],)).fetchall():
                    db.execute('UPDATE decisions SET feedback=? WHERE id=?', (_scrub(d['feedback'], card_needles), d['id']))
            if _table(db, 'draft_edits'):
                for d in db.execute('SELECT id,before_subject,before_body,after_subject,after_body FROM draft_edits WHERE suggestion_id=?', (card['id'],)).fetchall():
                    db.execute('UPDATE draft_edits SET before_subject=?,before_body=?,after_subject=?,after_body=? WHERE id=?',
                               (*(_scrub(d[k], card_needles) for k in ('before_subject', 'before_body', 'after_subject', 'after_body')), d['id']))
    if _table(db, 'promises'):
        for pr in db.execute('SELECT id,who,what,note FROM promises').fetchall():
            if mentions(pr['who'], needles) or mentions(pr['what'], needles) or mentions(pr['note'], needles):
                db.execute('UPDATE promises SET who=?,what=?,note=? WHERE id=?', (_scrub(pr['who'], needles), _scrub(pr['what'], needles), _scrub(pr['note'], needles), pr['id']))
    return {'identifiers': len(ids), 'events': len(dropped), 'facts': len(facts), 'cards': cards, 'needles': needles}
