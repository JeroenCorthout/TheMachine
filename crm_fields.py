"""Custom fields and tags on Graph records (docs/help/graph.md, "Fields and tags").

Fields: the owner adds their own fields per record type (person, organization, project), each one of four simple
types: text, number, date or a choice from a list. A value is kept on the record's id it was set on (a person's
identity, so it stays through merges; an organization's website, so it follows "Same organization as"), and sealed at
rest with the rest of what the owner writes (message_seal.DERIVED).

Tags: short labels on any record, as many as 20. The graph build puts them on the node (info.tags), so the lists
filter on them in SQL (crm.list_query), and the filter sheet offers the ones in use.

On a record shared with the team (shared_graph.py), fields and tags travel with it under the same rules as the rest
of the record: only on records that are shared anyway."""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3

KINDS = ('person', 'org', 'project')
TYPES = ('text', 'number', 'date', 'choice')
MAX_FIELDS = 30         # per record type
MAX_CHOICES = 30
MAX_TAGS = 20           # per record
MAX_TEXT = 500
TAG = re.compile(r'[^\s,;][^,;]{0,39}')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS crm_fields(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,name TEXT NOT NULL,type TEXT NOT NULL,choices TEXT,position INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_values(node_id TEXT NOT NULL,field_id INTEGER NOT NULL,value TEXT,set_at TEXT NOT NULL,PRIMARY KEY(node_id,field_id));
CREATE TABLE IF NOT EXISTS crm_tags(node_id TEXT NOT NULL,tag TEXT NOT NULL COLLATE NOCASE,at TEXT NOT NULL,PRIMARY KEY(node_id,tag));
'''


def ensure(db):
    db.executescript(SCHEMA)


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _field(row):
    return {'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'type': row['type'],
            **({'choices': json.loads(row['choices'] or '[]')} if row['type'] == 'choice' else {})}


def fields(db, kind=None):
    """The owner's fields, per record type in their order."""
    rows = db.execute('SELECT * FROM crm_fields WHERE ?1 IS NULL OR kind=?1 ORDER BY kind,position,id', (kind or None,))
    return [_field(r) for r in rows]


def _clean_name(name):
    name = ' '.join(str(name or '').split())
    if not name:
        raise ValueError('Give the field a name')
    if len(name) > 40:
        raise ValueError('Name: up to 40 characters')
    return name


def _clean_choices(raw):
    items = raw if isinstance(raw, list) else re.split(r'[\n,]', str(raw or ''))
    out = []
    for c in items:
        c = ' '.join(str(c or '').split())
        if c and c.casefold() not in {x.casefold() for x in out}:
            if len(c) > 40:
                raise ValueError('Each choice: up to 40 characters')
            out.append(c)
    if len(out) < 2:
        raise ValueError('A choice field needs at least two choices')
    if len(out) > MAX_CHOICES:
        raise ValueError(f'Up to {MAX_CHOICES} choices')
    return out


def save_field(db, args, now=None):
    """Add a field ({kind, name, type, choices}) or change one ({id, name, choices}). Its type stays what it was made
    as; a choice taken out of the list takes its values with it."""
    now = now or _now()
    name = _clean_name(args.get('name'))
    if args.get('id'):
        row = db.execute('SELECT * FROM crm_fields WHERE id=?', (int(args['id']),)).fetchone()
        if not row:
            raise ValueError('That field is gone')
        kind, kind_type = row['kind'], row['type']
    else:
        kind, kind_type = str(args.get('kind') or ''), str(args.get('type') or 'text')
        if kind not in KINDS:
            raise ValueError('Pick people, organizations or projects')
        if kind_type not in TYPES:
            raise ValueError('Pick text, number, date or choice')
        if db.execute('SELECT count(*) FROM crm_fields WHERE kind=?', (kind,)).fetchone()[0] >= MAX_FIELDS:
            raise ValueError(f'Up to {MAX_FIELDS} fields each')
    clash = db.execute('SELECT id FROM crm_fields WHERE kind=? AND name=? COLLATE NOCASE AND id!=?', (kind, name, int(args.get('id') or 0))).fetchone()
    if clash:
        raise ValueError('There is a field with that name already')
    choices = _clean_choices(args.get('choices')) if kind_type == 'choice' else None
    if args.get('id'):
        db.execute('UPDATE crm_fields SET name=?,choices=? WHERE id=?', (name, json.dumps(choices) if choices else None, row['id']))
        if choices:
            keep = {c.casefold() for c in choices}
            for v in db.execute('SELECT node_id,value FROM crm_values WHERE field_id=?', (row['id'],)).fetchall():
                if str(v['value'] or '').casefold() not in keep:
                    db.execute('DELETE FROM crm_values WHERE node_id=? AND field_id=?', (v['node_id'], row['id']))
        field_id = row['id']
    else:
        position = db.execute('SELECT coalesce(max(position),0)+1 FROM crm_fields WHERE kind=?', (kind,)).fetchone()[0]
        field_id = db.execute('INSERT INTO crm_fields(kind,name,type,choices,position,created_at) VALUES(?,?,?,?,?,?)',
                              (kind, name, kind_type, json.dumps(choices) if choices else None, position, now)).lastrowid
    return _field(db.execute('SELECT * FROM crm_fields WHERE id=?', (field_id,)).fetchone())


def delete_field(db, field_id):
    row = db.execute('SELECT id FROM crm_fields WHERE id=?', (int(field_id or 0),)).fetchone()
    if not row:
        raise ValueError('That field is gone')
    db.execute('DELETE FROM crm_values WHERE field_id=?', (row['id'],))
    db.execute('DELETE FROM crm_fields WHERE id=?', (row['id'],))
    return {'deleted': row['id']}


def clean_value(field, value):
    """A value as its field's type keeps it (a string), or None to clear it."""
    value = ' '.join(str(value if value is not None else '').split()) if field['type'] != 'text' else str(value if value is not None else '').strip()
    if not value:
        return None
    if field['type'] == 'text':
        if len(value) > MAX_TEXT:
            raise ValueError(f'{field["name"]}: up to {MAX_TEXT} characters')
        return value
    if field['type'] == 'number':
        try:
            number = float(value.replace(',', '').replace(' ', '').lstrip('€$£'))
        except ValueError:
            raise ValueError(f'{field["name"]}: a number, like 12000') from None
        if number != number or abs(number) > 1e15:
            raise ValueError(f'{field["name"]}: a number, like 12000')
        return str(int(number)) if number == int(number) else repr(round(number, 6))
    if field['type'] == 'date':
        try:
            return dt.date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            raise ValueError(f'{field["name"]}: a date, like 2026-12-15') from None
    found = next((c for c in field.get('choices') or [] if c.casefold() == value.casefold()), None)
    if not found:
        raise ValueError(f'{field["name"]}: one of {", ".join(field.get("choices") or [])}')
    return found


def _ids(db, row):
    """The ids a record's fields and tags can be kept under: a person's identities, an organization's merged ones."""
    import crm
    ids = set(crm.group_of(row))
    if row['kind'] == 'org':
        ids |= {a for a, t in crm.aliases(db).items() if t == row['id']}
    return sorted(ids)


def set_value(db, node_id, field_id, value, now=None):
    import crm
    row = crm.node_row(db, node_id)
    f = db.execute('SELECT * FROM crm_fields WHERE id=?', (int(field_id or 0),)).fetchone()
    if not f or f['kind'] != row['kind']:
        raise ValueError('That field is gone')
    field = _field(f)
    clean = clean_value(field, value)
    ids = _ids(db, row)
    db.execute('DELETE FROM crm_values WHERE field_id=? AND node_id IN (SELECT value FROM json_each(?))', (f['id'], json.dumps(ids)))
    if clean is not None:
        db.execute('INSERT INTO crm_values(node_id,field_id,value,set_at) VALUES(?,?,?,?)', (row['id'], f['id'], clean, now or _now()))
    return {'node_id': row['id'], 'field_id': f['id'], 'value': clean}


def values_for(db, row):
    """Every field of the record's type, with its value here (None when not set)."""
    ids = _ids(db, row)
    got = {}
    for v in db.execute('SELECT field_id,value,set_at FROM crm_values WHERE node_id IN (SELECT value FROM json_each(?)) ORDER BY set_at', (json.dumps(ids),)):
        got[v['field_id']] = v['value']
    return [{**f, 'value': got.get(f['id'])} for f in fields(db, row['kind'])]


def clean_tag(tag):
    tag = ' '.join(str(tag or '').split())
    if not tag or not TAG.fullmatch(tag):
        raise ValueError('A tag: up to 40 characters, no commas')
    return tag


def tag(db, node_id, label, add=True, now=None):
    import crm
    row = crm.node_row(db, node_id)
    label = clean_tag(label)
    ids = _ids(db, row)
    within = json.dumps(ids)
    if add:
        have = {r[0].casefold() for r in db.execute('SELECT tag FROM crm_tags WHERE node_id IN (SELECT value FROM json_each(?))', (within,))}
        if label.casefold() not in have:
            if len(have) >= MAX_TAGS:
                raise ValueError(f'Up to {MAX_TAGS} tags')
            same = db.execute('SELECT tag FROM crm_tags WHERE tag=? LIMIT 1', (label,)).fetchone()  # the spelling in use
            db.execute('INSERT INTO crm_tags(node_id,tag,at) VALUES(?,?,?)', (row['id'], same[0] if same else label, now or _now()))
    else:
        db.execute('DELETE FROM crm_tags WHERE tag=? AND node_id IN (SELECT value FROM json_each(?))', (label, within))
    tags = tags_for(db, row)
    info = json.loads(row['info'] or '{}')  # the build puts them on the node; until then the node says it already
    if tags:
        info['tags'] = tags
    else:
        info.pop('tags', None)
    db.execute('UPDATE graph_nodes SET info=? WHERE id=?', (json.dumps(info), row['id']))
    return {'node_id': row['id'], 'tags': tags}


def tags_for(db, row):
    ids = _ids(db, row)
    seen = {}
    for r in db.execute('SELECT tag FROM crm_tags WHERE node_id IN (SELECT value FROM json_each(?)) ORDER BY at,rowid', (json.dumps(ids),)):
        seen.setdefault(r[0].casefold(), r[0])
    return list(seen.values())[:MAX_TAGS]


def tags_in_use(db, limit=60):
    """The tags on records, most used first, for the filter sheet."""
    return [{'tag': r[0], 'count': r[1]} for r in db.execute(
        'SELECT tag,count(*) n FROM crm_tags GROUP BY tag COLLATE NOCASE ORDER BY n DESC,tag LIMIT ?', (limit,))]


def decorate(db, nodes, folded):
    """Put each record's tags on its node (info.tags) after the build. folded: crm.aliases."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='crm_tags'").fetchone():
        return nodes
    by_id = {}
    for r in db.execute('SELECT node_id,tag FROM crm_tags ORDER BY at,rowid'):
        by_id.setdefault(r[0], []).append(r[1])
    if not by_id:
        return nodes
    into = {}
    for alias, target in folded.items():
        into.setdefault(target, []).append(alias)
    out = []
    for n in nodes:
        if n[1] not in KINDS:
            out.append(n)
            continue
        if n[1] == 'person':
            if n[0] not in by_id and not any('"' + k + '"' in n[9] for k in by_id):
                out.append(n)
                continue
            info = json.loads(n[9])
            group = [n[0], *(info.get('identifiers') or ())]
        else:
            group = [n[0], *into.get(n[0], ())]
            if not any(k in by_id for k in group):
                out.append(n)
                continue
            info = json.loads(n[9])
        seen = {}
        for k in group:
            for t in by_id.get(k, ()):
                seen.setdefault(t.casefold(), t)
        if not seen:
            out.append(n)
            continue
        info['tags'] = list(seen.values())[:MAX_TAGS]
        out.append(tuple(n[:9]) + (json.dumps(info),))
    return out


def shared(db, row):
    """What goes with a record shared with the team: its fields with a value and its tags."""
    try:
        got = [{'name': f['name'], 'type': f['type'], 'value': f['value']} for f in values_for(db, row) if f['value'] is not None]
        tags = tags_for(db, row)
    except sqlite3.OperationalError:  # an old database without the tables: nothing to add
        return {}
    return {**({'fields': got[:MAX_FIELDS]} if got else {}), **({'tags': tags} if tags else {})}
