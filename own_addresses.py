"""The owner's own email addresses, found from the sources already connected (docs/intelligence.md, Your addresses).

Config `owner_emails` (top level or per account) stays the override; everything here only adds to it. Where each address
comes from:

- `account`: a connected mailbox or calendar of the owner's own (never one shared from a Fleet);
- `alias`: a Gmail send-as address (users.settings.sendAs.list, read with gmail.readonly, the scope mail already has) or an
  Outlook proxy address (Graph /me proxyAddresses, User.Read). Fetched once a day per mailbox (fetch_due, save_fetched);
- `calendar`: the `self` attendee on the owner's own calendar (never a calendar of someone else's that the owner reads);
- `slack`, `intercom`: the profile address of the owner's own Slack or Intercom sign-in;
- `received`: mail to it lands in the owner's mailbox at its domain, and it carries the owner's own full name (a Google
  Workspace alias that was never added as a send-as address);
- `named`: it carries the owner's own full name somewhere else (an invite, a To line): only ever a maybe;
- `sent`: the From of mail sent from one of the owner's mailboxes (a mailbox may send as a shared or delegated address).

Shared addresses never count as the owner's. An address is `shared` when a teammate in a Fleet also uses it, when mail
from it comes under two or more people's names (a Google Group's "Ann via Sales"), or when Gmail sends it through someone
else's mail server. It is `maybe` when unsure: a role-like address (info@, sales@, support@, team@), a Gmail send-as
address that isn't set as an alias, or one only seen in sent mail. A `maybe` is listed in Settings, Account, for the
owner to confirm with one tap; it counts only once confirmed. A removed address never counts again, and neither do the
ones in config `not_owner_emails`."""
from __future__ import annotations

import json
import re

SCHEMA = '''
CREATE TABLE IF NOT EXISTS own_addresses(address TEXT PRIMARY KEY,sources TEXT NOT NULL,state TEXT NOT NULL,reason TEXT,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS own_address_choices(address TEXT PRIMARY KEY,choice TEXT NOT NULL,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS own_address_fetched(account_id TEXT PRIMARY KEY,entries TEXT,tried_at TEXT NOT NULL);
'''
ADDRESS = re.compile(r'^[^@\s<>",;]{1,64}@[a-z0-9.-]{1,190}\.[a-z]{2,}$')
# A local part more than one person tends to read: role and team inboxes, also with a suffix (sales-eu@, support.nl@)
ROLE = re.compile(r'^(?:info|support|hello|hi|team|sales|billing|admin|office|contact|help|accounts?|finance|jobs|careers|hr|press|'
                  r'legal|security|privacy|feedback|marketing|partners?|partnerships?|success|cs|service|orders|invoices?|ops|operations|dev|'
                  r'engineering|product|founders|all|everyone|staff|general|enquiries|inquiries|reception|bookings?|events|'
                  r'community|media|pr|noreply|no-reply|mail|post|shop|store|webmaster|postmaster)(?:[._+-][a-z0-9]+)?@')
MAIL_SERVICES = ('gmail', 'outlook')
CALENDAR_SERVICES = ('calendar', 'outlook_calendar')
LABELS = {'config': 'Settings file', 'account': 'Connected account', 'alias': 'Alias', 'calendar': 'Your calendar',
          'slack': 'Slack profile', 'intercom': 'Intercom profile', 'received': 'Mail to you', 'named': 'Has your name',
          'sent': 'Mail you sent', 'confirmed': 'You confirmed it'}
STRONG = {'config', 'account', 'alias', 'calendar', 'slack', 'intercom', 'received'}
FETCH_EVERY_DAYS = 1
SENT_ONLY = 'Only seen in mail you sent'
NAMED_ONLY = 'Has your name'


def ensure(db):
    for statement in filter(str.strip, SCHEMA.split(';')):  # never executescript: it would commit a build's transaction
        db.execute(statement)


def clean(value):
    address = str(value or '').strip().lower()
    return address if ADDRESS.match(address) else ''


def role_like(address):
    return bool(ROLE.match(address))


def own_accounts(config):
    """The owner's own connected accounts: never one shared with them from a Fleet."""
    return [a for a in config.get('accounts', []) if isinstance(a, dict) and not a.get('shared')]


def own_calendar(account):
    """A calendar account that reads the owner's own calendar (its primary), not someone else's they can see."""
    calendar = str(account.get('calendar_id') or 'primary').strip().lower()
    return account.get('service') in CALENDAR_SERVICES and calendar in ('primary', clean(account.get('email')))


def configured(config):
    """The override from config: owner_emails (top level and per account) in, not_owner_emails out."""
    found = {clean(e) for e in config.get('owner_emails') or ()}
    for a in own_accounts(config):
        found |= {clean(e) for e in a.get('owner_emails') or ()}
    return found - {''}, {clean(e) for e in config.get('not_owner_emails') or ()} - {''}


def primaries(config):
    """The addresses of the owner's own mailboxes and calendars, and of their own Slack and Intercom sign-ins."""
    found = {}
    for a in own_accounts(config):
        address, service = clean(a.get('email')), a.get('service', 'gmail')
        if not address:
            continue
        if service in MAIL_SERVICES or own_calendar(a):
            found.setdefault(address, set()).add('account')
        elif service == 'slack':
            found.setdefault(address, set()).add('slack')
        elif service == 'intercom':
            found.setdefault(address, set()).add('intercom')
    return found


def send_as_entries(answer, primary):
    """A mailbox's send-as list in one shape: [{address, primary, alias, name, other_server}]. Gmail's sendAs (only
    verified ones), or Outlook's in the same shape (ms_graph.py)."""
    out = []
    for entry in (answer or {}).get('sendAs') or ():
        if not isinstance(entry, dict):
            continue
        address = clean(entry.get('sendAsEmail'))
        if not address or entry.get('verificationStatus') not in (None, 'accepted'):
            continue
        smtp = entry.get('smtpMsa') if isinstance(entry.get('smtpMsa'), dict) else {}
        user = str(smtp.get('username') or '').strip().lower()
        # Sends through another mail server signed in as someone else: that's their mailbox, not an alias of this one
        other = bool(smtp) and bool(user) and user != address and user.split('@')[0] != address.split('@')[0]
        out.append({'address': address, 'primary': bool(entry.get('isPrimary')) or address == clean(primary),
                    'alias': entry.get('treatAsAlias') is not False, 'name': str(entry.get('displayName') or '')[:120], 'other_server': other})
    return out


def fetch_due(db, account_id, now):
    import datetime as dt
    ensure(db)
    row = db.execute('SELECT tried_at FROM own_address_fetched WHERE account_id=?', (account_id,)).fetchone()
    if not row:
        return True
    try:
        return dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(row[0]) >= dt.timedelta(days=FETCH_EVERY_DAYS)
    except ValueError:
        return True


def save_fetched(db, account_id, entries, now):
    """Keep a mailbox's send-as list (None: the read failed; the last good list stays)."""
    ensure(db)
    if entries is None:
        db.execute('INSERT INTO own_address_fetched(account_id,entries,tried_at) VALUES(?,NULL,?) '
                   'ON CONFLICT(account_id) DO UPDATE SET tried_at=excluded.tried_at', (account_id, now))
    else:
        db.execute('INSERT OR REPLACE INTO own_address_fetched(account_id,entries,tried_at) VALUES(?,?,?)', (account_id, json.dumps(entries), now))


def mail_accounts(config):
    """The owner's own mailboxes whose send-as list is read."""
    return [a for a in own_accounts(config) if a.get('service', 'gmail') in MAIL_SERVICES and clean(a.get('email'))]


def fetched(db, config):
    ensure(db)
    ids = {a['id'] for a in mail_accounts(config) if a.get('id')}
    out = {}
    for r in db.execute('SELECT account_id,entries FROM own_address_fetched WHERE entries IS NOT NULL'):
        if r[0] in ids:
            try:
                out[r[0]] = [e for e in json.loads(r[1]) if isinstance(e, dict)]
            except ValueError:
                continue
    return out


def people(names):
    """How many people a set of full names is: "Sam Lee", "Lee Sam" and "Sam Leeman" are one (same first word or the
    same words), "Sam Lee" and "Kim Park" two."""
    same = lambda a, b: a.split()[0] == b.split()[0] or set(a.split()) == set(b.split())
    groups = []
    for name in sorted(names):
        joined = [g for g in groups if any(same(name, n) for n in g)]
        groups = [g for g in groups if g not in joined] + [[name, *(n for g in joined for n in g)]]
    return len(groups)


def evidence(config, signals):
    """From the stored signals: addresses the owner's mailboxes sent from, the `self` address on the owner's own
    calendars, the people's names each address wrote under (From, and invites), the names others give it (To and Cc
    lines, invites), who sent mail that landed under it in the owner's mailbox at its own domain, and the owner's own
    full names (from what they sent from their own accounts, and owner_name). signals: (id, account_id, kind, at, metadata)."""
    import graph
    accounts = {a.get('id'): a for a in own_accounts(config)}
    mine = set(primaries(config))
    sent, selves, names, called, received = set(), set(), {}, {}, {}
    owner = {graph.plain_name(config.get('owner_name'))} - {None}
    note = lambda store, address, name: store.setdefault(address, set()).add(name) if name else None
    for _, account_id, kind, _, m in signals:
        if kind == 'gmail.message':
            labels = m.get('labels') or ()
            sender = graph.addresses(m.get('from'))[:1]
            if sender:
                name, address = sender[0]
                full = graph.plain_name(name)
                if 'SENT' in labels and account_id in accounts:
                    sent.add(address)
                    if address in mine and full:
                        owner.add(full)
                note(names, address, full)  # a From name is the sender's own, never proof of whose address it is
            box = clean(accounts[account_id].get('email')) if account_id in accounts else ''
            for name, address in graph.addresses(m.get('to')) + graph.addresses(m.get('cc')):
                note(called, address, graph.plain_name(name))
                if box and sender and 'SENT' not in labels and address.rsplit('@', 1)[-1] == box.rsplit('@', 1)[-1]:
                    note(received, address, sender[0][1])
        elif kind == 'calendar.event':
            own = account_id in accounts and own_calendar(accounts[account_id])
            for a in [*(m.get('attendees') or ()), m.get('organizer')]:
                if not isinstance(a, dict) or not clean(a.get('email')):
                    continue
                full = graph.plain_name(a.get('displayName'))
                note(names, clean(a['email']), full)
                note(called, clean(a['email']), full)
                if own and a.get('self'):
                    selves.add(clean(a['email']))
    return {'sent': sent, 'calendar': selves, 'names': names, 'called': called, 'received': received, 'owner_names': owner}


def detect(config, sendas, found, teammates=()):
    """{address: {'sources', 'state', 'reason'}} for every address that may be the owner's. state: 'yours', 'maybe' or
    'shared'. sendas: {account_id: send_as_entries}; found: evidence(); teammates: the addresses of people in the
    owner's Fleets."""
    team = {clean(e) for e in teammates} - {''}
    pinned, _ = configured(config)
    sources, notes = {}, {}
    add = lambda address, source: sources.setdefault(address, set()).add(source) if clean(address) else None
    for address in pinned:
        add(address, 'config')
    for address, kinds in primaries(config).items():
        for kind in kinds:
            add(address, kind)
    for account_id, entries in sendas.items():
        for e in entries:
            if e.get('primary'):
                add(e['address'], 'account')
                continue
            add(e['address'], 'alias' if e.get('alias') else 'sendas')
            if e.get('other_server'):
                notes.setdefault(e['address'], set()).add('other_server')
    for address in found.get('calendar') or ():
        add(address, 'calendar')
    for address in found.get('sent') or ():
        add(address, 'sent')
    owner = found.get('owner_names') or set()
    for address, called in (found.get('called') or {}).items():  # it carries the owner's own full name
        if owner and called & owner:
            # and mail from at least two people lands under it in the owner's mailbox at its domain: one To line proves little
            add(address, 'received' if len((found.get('received') or {}).get(address, ())) >= 2 else 'named')
    out = {}
    for address, got in sources.items():
        state, reason = classify(address, got, people((found.get('names') or {}).get(address, ())), address in team,
                                 notes.get(address, set()))
        out[address] = {'sources': sorted(got), 'state': state, 'reason': reason}
    return out


def classify(address, sources, names, teammate, notes=()):
    """(state, reason) for one address. Settings and the owner's own connected accounts are always theirs; anything
    else is theirs only with nothing that says other people use it too."""
    if 'config' in sources:
        return 'yours', None
    if 'account' in sources:
        return 'yours', None  # the owner's own sign-in: what it always was
    if teammate:
        return 'shared', 'A teammate uses it too'
    if names >= 2:
        return 'shared', 'Mail from it comes under several names'
    if 'other_server' in notes:
        return 'shared', 'Gmail sends it through another account'
    if role_like(address):
        return 'maybe', 'Looks like a team address'
    if 'sendas' in sources and not sources & (STRONG - {'account'}):
        return 'maybe', 'Gmail sends as it, but not as an alias'
    if not sources & STRONG:
        return 'maybe', NAMED_ONLY if 'named' in sources else SENT_ONLY
    return 'yours', None


def save(db, detected, now):
    """Keep what detect found; addresses no longer found go."""
    ensure(db)
    db.execute('DELETE FROM own_addresses WHERE address NOT IN (SELECT value FROM json_each(?))', (json.dumps(sorted(detected)),))
    db.executemany('INSERT INTO own_addresses(address,sources,state,reason,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(address) DO UPDATE SET '
                   'sources=excluded.sources,state=excluded.state,reason=excluded.reason,updated_at=excluded.updated_at',
                   [(a, json.dumps(d['sources']), d['state'], d['reason'], now) for a, d in sorted(detected.items())])


def refresh(db, config, signals, teammates, now):
    """Detect again from the signals and the kept send-as lists, and keep the result."""
    found = detect(config, fetched(db, config), evidence(config, signals), teammates)
    save(db, found, now)
    return found


def choices(db):
    ensure(db)
    return {r[0]: r[1] for r in db.execute('SELECT address,choice FROM own_address_choices')}


def choose(db, address, choice, now):
    """The owner's word on one address: 'confirm' (a maybe is theirs) or 'remove' (never theirs)."""
    address = clean(address)
    if not address or choice not in ('confirm', 'remove'):
        raise ValueError('Pick an address')
    ensure(db)
    row = db.execute('SELECT state FROM own_addresses WHERE address=?', (address,)).fetchone()
    if not row:
        raise ValueError('That address isn’t on the list anymore')
    db.execute('INSERT OR REPLACE INTO own_address_choices(address,choice,at) VALUES(?,?,?)', (address, choice, now))


def yours(db, config=None):
    """The owner's own addresses: config's, the detected ones with nothing against them, and the maybes the owner
    confirmed; never one they removed or one in not_owner_emails."""
    pinned, excluded = configured(config or {})
    try:
        ensure(db)
        made = {r[0]: r[1] for r in db.execute('SELECT address,state FROM own_addresses')}
        chosen = choices(db)
    except Exception:  # a read-only or locked database: config alone
        made, chosen = {}, {}
    found = {a for a, s in made.items() if s == 'yours' or (s == 'maybe' and chosen.get(a) == 'confirm')}
    return pinned | (found - {a for a, c in chosen.items() if c == 'remove'} - excluded)


def excluded(db, config=None):
    """Addresses the owner said aren't theirs."""
    _, out = configured(config or {})
    try:
        return out | {a for a, c in choices(db).items() if c == 'remove'}
    except Exception:
        return out


def view(db, config):
    """Settings, Account: the owner's addresses with where each came from, and the maybes to confirm."""
    ensure(db)
    pinned, out_ = configured(config)
    chosen = choices(db)
    mailbox = {a.get('id'): a.get('service', 'gmail') for a in mail_accounts(config)}
    outlook = any(s == 'outlook' for s in mailbox.values())
    gmail = any(s == 'gmail' for s in mailbox.values())
    alias = 'Outlook alias' if outlook and not gmail else 'Gmail alias' if gmail and not outlook else LABELS['alias']
    label = lambda s: alias if s == 'alias' else {'sendas': 'Gmail send-as'}.get(s, LABELS.get(s, s))
    mine, maybe = [], []
    for r in db.execute('SELECT address,sources,state,reason FROM own_addresses ORDER BY address'):
        address, sources, state, reason = r[0], json.loads(r[1]), r[2], r[3]
        if address in out_ or chosen.get(address) == 'remove':
            continue
        if state == 'yours' or (state == 'maybe' and chosen.get(address) == 'confirm'):
            if state == 'maybe':
                sources = [*sources, 'confirmed']
            fixed = address in pinned or 'account' in sources
            mine.append({'address': address, 'sources': [label(s) for s in sources], 'removable': not fixed})
        elif state == 'maybe':
            said = {SENT_ONLY: 'sent', NAMED_ONLY: 'named'}.get(reason)
            maybe.append({'address': address, 'sources': [label(s) for s in sources if s != said], 'why': reason})
    order = lambda x: (not any(s in ('Connected account', 'Settings file') for s in x['sources']), x['address'])
    return {'yours': sorted(mine, key=order), 'maybe': maybe}
