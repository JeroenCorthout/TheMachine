"""Security levels: Standard (today's defaults) and Careful, one switch instead of many (docs/help/security-levels.md).

Two places pick a level:
- Settings, Security: the owner's own level, for this Machine, their Family rules and their Friends rules. It is a
  starting point: picking it sets the settings below; changing one afterwards shows Custom. Nothing is stored about
  the level itself, so it is always read from the settings as they are.
- A Work Fleet's owner or admin picks the Fleet's level on the account page. The control plane carries it in the
  directory ({'security': {'level': 'careful'}}). Careful there is a floor for this Machine's answers in that Fleet:
  no Always (every answer waits for the owner, or Never), Without names, only people from work accounts, no
  attachments in the Shared Graph, no card text in phone notifications for work cards, and no contact lookups.
  A member's own stricter choices stay; their looser stored choices are kept but count as the floor while it lasts.

Neither level changes what needs the owner's OK to go out (every send, calendar write and final step waits for their
Send or confirm in both), nor Do it, its tools or coding agents. Standard changes nothing for anyone."""
from __future__ import annotations

import json
import sqlite3

LEVELS = ('standard', 'careful')
# The owner's own settings a level sets: key -> (Standard, Careful). Standard is exactly today's default.
OWN = {
    'notify_text': ('on', 'off'),       # card text in phone notifications
    'save_logins': ('on', 'off'),       # "Save for next time" offered when signing in to a site
    'learnings': ('on', 'off'),         # Offer to share learnings (team_learnings.py)
    'people_lookup': ('on', 'off'),     # Look up work contacts (enrichment.py)
    'keep_days': ('90', '30'),          # Do it tool calls and run evidence kept this long (retention.py)
}
FAMILY = {'availability': ('always', 'ask')}   # family.DEFAULT_RULES: free/busy times
FRIENDS = {'meeting': ('ask', 'never')}        # Friends: meeting context
ALL = '*'  # fleet.ALL: the rule for everyone in a group
SCHEMA = 'CREATE TABLE IF NOT EXISTS security_prefs(key TEXT PRIMARY KEY,value TEXT NOT NULL)'
OWN_PREFS = ('notify_text', 'save_logins', 'keep_days')  # kept in security_prefs; the others where they always were


def _get(db, sql, args=()):
    try:
        row = db.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _put(db, sql, args):
    db.execute(sql, args)


def pref(db, key):
    """One of the owner's own settings, as its stored value ('on'/'off', or days for keep_days)."""
    default = OWN[key][0]
    if key in OWN_PREFS:
        value = _get(db, 'SELECT value FROM security_prefs WHERE key=?', (key,))
        return value if value in OWN[key] else default
    if key == 'learnings':
        return 'off' if _get(db, "SELECT value FROM team_learning_prefs WHERE key='offer'") == 'off' else 'on'
    return 'off' if _get(db, "SELECT value FROM enrich_prefs WHERE key='people'") == 'off' else 'on'


def set_pref(db, key, value):
    if key not in OWN or value not in OWN[key]:
        raise ValueError('Unknown setting')
    if key in OWN_PREFS:
        db.execute(SCHEMA)
        _put(db, 'INSERT INTO security_prefs(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
    elif key == 'learnings':
        db.execute('CREATE TABLE IF NOT EXISTS team_learning_prefs(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        _put(db, "INSERT INTO team_learning_prefs(key,value) VALUES('offer',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
    else:
        db.execute('CREATE TABLE IF NOT EXISTS enrich_prefs(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        _put(db, "INSERT INTO enrich_prefs(key,value) VALUES('people',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))


# -- the directory: Work Fleets and their level
def directory(db):
    try:
        raw = _get(db, "SELECT value FROM fleet_meta WHERE key='directory'")
        found = json.loads(raw) if raw else {}
    except ValueError:
        found = {}
    return [f for f in (found.get('fleets') or []) if isinstance(f, dict)] if isinstance(found, dict) else []


def fleet_level(group):
    """A Fleet's level as its directory entry says (only Work Fleets have one); anything else is Standard."""
    if not isinstance(group, dict) or group.get('kind') != 'work':
        return 'standard'
    level = (group.get('security') or {}).get('level') if isinstance(group.get('security'), dict) else None
    return 'careful' if level == 'careful' else 'standard'


def careful_fleets(db):
    return [f for f in directory(db) if fleet_level(f) == 'careful']


def group_ref(db, kind):
    return next((f.get('id') for f in directory(db) if f.get('kind') == kind and f.get('id')), None)


# -- what applies (the owner's own setting, and any Work Fleet floor)
def notify_text(db, work=False):
    """Phone notifications show the card's text: off by the owner's own choice, or for a work card in a Careful Fleet."""
    return pref(db, 'notify_text') == 'on' and not (work and careful_fleets(db))


def save_logins(db):
    return pref(db, 'save_logins') == 'on'


def people_lookup(db):
    return pref(db, 'people_lookup') == 'on' and not careful_fleets(db)


def keep_days(db):
    return int(pref(db, 'keep_days'))


def clamp(level, rule, details):
    """A rule as it applies under a Fleet's level: Careful has no Always and no names."""
    if level != 'careful':
        return rule, details
    return ('ask' if rule == 'always' else rule), False


# -- the owner's own level
def _rule(db, ref, kind, default):
    return _get(db, 'SELECT rule FROM fleet_rules WHERE fleet=? AND member_id=? AND kind=?', (ref, ALL, kind)) or default


def current(db):
    """{key: value} of every setting the owner's level covers, as it is now (Family and Friends only when in one)."""
    out = {k: pref(db, k) for k in OWN}
    for kind, refs, table in (('family', FAMILY, 'family'), ('friends', FRIENDS, 'friends')):
        ref = group_ref(db, kind)
        if ref:
            out.update({f'{table}_{k}': _rule(db, ref, k, values[0]) for k, values in refs.items()})
    return out


def values(level, keys):
    i = LEVELS.index(level)
    table = {**{k: v[i] for k, v in OWN.items()}, **{'family_' + k: v[i] for k, v in FAMILY.items()},
             **{'friends_' + k: v[i] for k, v in FRIENDS.items()}}
    return {k: table[k] for k in keys}


def label(db):
    now = current(db)
    return next((level for level in LEVELS if values(level, now) == now), 'custom')


def apply(db, level):
    """Sets the owner's own settings the level covers, and returns the Family and Friends rules still to set, as
    [(group id, kind, rule)]: the caller sets them the way the dashboard does (fleet.Fleet.set_rule), after this
    transaction, so waiting questions settle as they would."""
    if level not in LEVELS:
        raise ValueError('A level is Standard or Careful')
    now = current(db)
    want = values(level, now)
    rules = []
    for key in (k for k in now if now[k] != want[k]):
        if key in OWN:
            set_pref(db, key, want[key])
        else:
            group, kind = key.split('_', 1)
            rules.append((group_ref(db, group), kind, want[key]))
    return rules


def view(db):
    """Settings, Security: the owner's level, what it covers now, and each Work Fleet's level."""
    now = current(db)
    return {'level': label(db), 'settings': now,
            'careful': values('careful', now), 'standard': values('standard', now),
            'fleets': [{'id': f.get('id'), 'name': str(f.get('name') or 'Work Fleet')[:80], 'level': fleet_level(f)}
                       for f in directory(db) if f.get('kind') == 'work'],
            'floor': bool(careful_fleets(db))}
