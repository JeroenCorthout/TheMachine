"""Import a spreadsheet (CSV) of people, organizations or projects into the Graph, on the Machine.

The owner uploads a file on the dashboard; nothing of it leaves the Machine. The columns are guessed from their
headers (and can be changed), and a preview shows each row as new, matching one already in the Graph (to update), or
skipped with why: no name, an address that isn't one, the same row twice in the file. A match is the same email for a
person (else the same name at the same organization), the same website for an organization (any of its websites, else
the same name), and the same name for a project.

Import makes the new ones as if added by hand and fills in what the file says for the ones that match (an empty cell
leaves a field as it is). The last import can be undone: what it made goes again and what it changed goes back to how
it was (graph_imports keeps that, sealed like the projects' notes it can hold)."""
from __future__ import annotations

import csv
import io
import json
import re

import crm
import graph

MAX_ROWS = 5000
MAX_COLUMNS = 60
SAMPLES = 3

# field: (shown name, header words that point to it)
FIELDS = {
    'person': {'name': ('Name', ('name', 'full name', 'contact', 'contact name', 'person')),
               'first_name': ('First name', ('first name', 'firstname', 'given name', 'first')),
               'last_name': ('Last name', ('last name', 'lastname', 'surname', 'family name', 'last')),
               'email': ('Email', ('email', 'e-mail', 'email address', 'mail', 'work email')),
               'phone': ('Phone', ('phone', 'mobile', 'telephone', 'phone number', 'cell', 'tel')),
               'org': ('Organization', ('organisation', 'organization', 'company', 'account', 'company name', 'employer')),
               'title': ('Title', ('title', 'job title', 'position', 'role', 'function')),
               'location': ('Location', ('location', 'city', 'country', 'address')),
               'linkedin_url': ('LinkedIn', ('linkedin', 'linkedin url', 'linkedin profile')),
               'notes': ('Notes', ('notes', 'note', 'description', 'comments', 'comment'))},
    'org': {'name': ('Name', ('name', 'organisation', 'organization', 'company', 'company name', 'account', 'account name')),
            'website': ('Website', ('website', 'domain', 'url', 'web', 'site', 'homepage', 'company website')),
            'linkedin_url': ('LinkedIn', ('linkedin', 'linkedin url', 'linkedin page')),
            'industry': ('Industry', ('industry', 'sector', 'vertical')),
            'size': ('Size', ('size', 'employees', 'headcount', 'company size', 'number of employees')),
            'location': ('Location', ('location', 'city', 'country', 'address', 'hq')),
            'founded': ('Founded', ('founded', 'year founded', 'founded year')),
            'about': ('About', ('about', 'description', 'summary')),
            'notes': ('Notes', ('notes', 'note', 'comments', 'comment'))},
    'project': {'name': ('Name', ('name', 'project', 'deal', 'opportunity', 'deal name', 'project name', 'title')),
                'org': ('Organization', ('organisation', 'organization', 'company', 'account', 'website', 'domain')),
                'board': ('Board', ('board', 'pipeline')),
                'stage': ('Stage', ('stage', 'status', 'deal stage', 'phase')),
                'value': ('Value', ('value', 'amount', 'deal value', 'revenue', 'price', 'size')),
                'close_date': ('Expected close', ('close date', 'expected close', 'closing date', 'close', 'expected close date', 'due date')),
                'notes': ('Notes', ('notes', 'note', 'description', 'comments', 'comment'))}}
KINDS = {'person': 'people', 'org': 'organisations', 'project': 'projects'}


def _key(text):
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower().replace('e-mail', 'email')).split())


def read(text):
    """(headers, rows) from the file's text: comma, semicolon or tab separated, a first row of headers."""
    text = str(text or '').lstrip('﻿')
    if not text.strip():
        raise ValueError('That file is empty')
    if len(text) > 8 * 1024 * 1024:
        raise ValueError('That file is too big: up to 8 MB')
    head = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(head, delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError('The file needs a row of headers and at least one row under it')
    headers = [' '.join(h.split())[:80] or f'Column {i + 1}' for i, h in enumerate(rows[0][:MAX_COLUMNS])]
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError(f'Up to {MAX_ROWS} rows at a time')
    return headers, [[c.strip() for c in r[:len(headers)]] + [''] * (len(headers) - len(r)) for r in rows[1:]]


def guess(kind, headers):
    """{column index: field} from the headers: the exact words first, then a header that contains them."""
    fields, out, taken = FIELDS[kind], {}, set()
    keys = [_key(h) for h in headers]
    for exact in (True, False):
        for i, k in enumerate(keys):
            if i in out:
                continue
            for field, (_, words) in fields.items():
                if field in taken:
                    continue
                if (k in words) if exact else any(w in k.split() or (len(w) > 4 and w in k) for w in words):
                    out[i] = field
                    taken.add(field)
                    break
    return out


def clean_mapping(kind, headers, mapping):
    if mapping is None:
        return guess(kind, headers)
    out, seen = {}, set()
    for i, field in (mapping.items() if isinstance(mapping, dict) else enumerate(mapping or ())):
        i = int(i)
        if not field or not 0 <= i < len(headers):
            continue
        if field not in FIELDS[kind]:
            raise ValueError(f'{field} is not a field you can import')
        if field in seen:
            raise ValueError(f'{FIELDS[kind][field][0]} is picked for two columns')
        seen.add(field)
        out[i] = field
    return out


def record(kind, row, mapping):
    """One row as {field: value}: names put together, a website as its domain."""
    out = {field: row[i] for i, field in mapping.items() if row[i]}
    if kind == 'person':
        if not out.get('name') and (out.get('first_name') or out.get('last_name')):
            out['name'] = ' '.join(filter(None, (out.get('first_name'), out.get('last_name'))))
        out.pop('first_name', None)
        out.pop('last_name', None)
        if out.get('email'):
            out['email'] = out['email'].strip().lower().removeprefix('mailto:')
    if out.get('name'):
        out['name'] = ' '.join(out['name'].split())
    return out


def _org_by_name(db, name):
    row = db.execute("SELECT id,name FROM graph_nodes WHERE kind='org' AND name=? COLLATE NOCASE ORDER BY strength DESC LIMIT 1", (name,)).fetchone()
    return row


def match(db, kind, rec):
    """(node_id or None, why or None): who in the Graph this row is; why says how it matched."""
    if kind == 'person':
        if rec.get('email'):
            found = graph.resolve(db, 'email:' + rec['email'])
            if found:
                return found['id'], 'Same email'
        if rec.get('name'):
            org_ids = crm.org_ids(db, rec['org']) if rec.get('org') else []
            for r in db.execute("SELECT id,info FROM graph_nodes WHERE kind='person' AND name=? COLLATE NOCASE ORDER BY strength DESC LIMIT 5", (rec['name'],)):
                if org_ids and json.loads(r['info'] or '{}').get('org') in org_ids:
                    return r['id'], 'Same name and organization'
        return None, None
    if kind == 'org':
        domain = graph.website_domain(rec.get('website')) if rec.get('website') else None
        if domain:
            found = db.execute("SELECT id FROM graph_nodes WHERE id=? AND kind='org'", (crm.canonical(db, 'org:' + domain),)).fetchone()
            if found:
                return found['id'], 'Same website'
        found = _org_by_name(db, rec['name']) if rec.get('name') else None
        return (found['id'], 'Same name') if found else (None, None)
    found = db.execute('SELECT id FROM projects WHERE name=? COLLATE NOCASE', (rec.get('name') or '',)).fetchone()
    return ('project:' + str(found['id']), 'Same name') if found else (None, None)


def check(kind, rec):
    """Why a row can't be imported, or None."""
    if not rec.get('name') and not (kind == 'person' and rec.get('email')):
        return 'No name'
    if len(rec.get('name') or '') > (60 if kind == 'project' else 100):
        return 'Name too long'
    if kind == 'person' and rec.get('email') and not graph.EMAIL.fullmatch(rec['email']):
        return 'Not an email address'
    if kind == 'org' and rec.get('website') and not graph.website_domain(rec['website']):
        return 'Not a website'
    if kind == 'project' and rec.get('value'):
        try:
            float(rec['value'].replace(',', '').replace(' ', '').lstrip('€$£'))
        except ValueError:
            return 'Value is not a number'
    if kind == 'project' and rec.get('close_date') and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', rec['close_date']):
        return 'Close date is not like 2026-12-15'
    return None


def _same_key(kind, rec):
    if kind == 'person':
        return rec.get('email') or ('name', (rec.get('name') or '').casefold(), (rec.get('org') or '').casefold())
    if kind == 'org':
        return graph.website_domain(rec.get('website')) if rec.get('website') else ('name', (rec.get('name') or '').casefold())
    return (rec.get('name') or '').casefold()


def plan(db, kind, text, mapping=None):
    """Each row with what Import would do: new, match (with the record it updates) or skip (with why)."""
    if kind not in FIELDS:
        raise ValueError('Import people, organizations or projects')
    headers, rows = read(text)
    mapping = clean_mapping(kind, headers, mapping)
    if 'name' not in mapping.values() and not (kind == 'person' and ({'email', 'first_name', 'last_name'} & set(mapping.values()))):
        raise ValueError('Pick the column with the name')
    out, seen = [], set()
    for n, row in enumerate(rows, start=2):
        rec = record(kind, row, mapping)
        why = check(kind, rec)
        if why:
            out.append({'line': n, 'status': 'skip', 'why': why, 'record': rec})
            continue
        if kind == 'person' and not rec.get('name'):
            rec['name'] = rec['email'].split('@', 1)[0].replace('.', ' ').title()
        key = _same_key(kind, rec)
        if key in seen:
            out.append({'line': n, 'status': 'skip', 'why': 'Twice in the file', 'record': rec})
            continue
        seen.add(key)
        node_id, how = match(db, kind, rec)
        out.append({'line': n, 'status': 'match' if node_id else 'new', 'node_id': node_id, 'why': how, 'record': rec})
    return headers, rows, mapping, out


def preview(db, kind, text, mapping=None, limit=200):
    """What the dashboard shows before Import: the columns with the field each goes to and a few values, the counts,
    and the first rows with what happens to each."""
    headers, rows, mapping, planned = plan(db, kind, text, mapping)
    ids = sorted({p['node_id'] for p in planned[:limit] if p.get('node_id')})
    names = dict(db.execute(f"SELECT id,name FROM graph_nodes WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()) if ids else {}
    columns = [{'index': i, 'header': h, 'field': mapping.get(i), 'samples': [r[i] for r in rows if r[i]][:SAMPLES]} for i, h in enumerate(headers)]
    counts = {s: sum(1 for p in planned if p['status'] == s) for s in ('new', 'match', 'skip')}
    shown = [{**p, 'match_name': names.get(p.get('node_id'))} for p in planned[:limit]]
    return {'kind': kind, 'columns': columns, 'fields': [{'id': f, 'name': v[0]} for f, v in FIELDS[kind].items()],
            'counts': counts, 'rows': shown, 'total': len(planned)}


# ------------------------------------------------------------------ import and undo

# What an import can change, and how each goes back: fixed statements per table (no table or column names from data)
PROJECT_COLUMNS = ('org', 'value', 'close_date', 'notes', 'board_id', 'stage', 'stage_reason', 'stage_event_id', 'stage_by', 'stage_at',
                   'outcome', 'outcome_reason', 'sort_run', 'sort_tries', 'sort_note')
STATEMENTS = {
    'person_edits': ('SELECT * FROM person_edits WHERE identifier=?', 'DELETE FROM person_edits WHERE identifier=?',
                     'INSERT INTO person_edits(identifier,fields,updated_at) VALUES(:identifier,:fields,:updated_at)'),
    'person_orgs': ('SELECT * FROM person_orgs WHERE identifier=?', 'DELETE FROM person_orgs WHERE identifier=?',
                    'INSERT INTO person_orgs(identifier,org,org_id,source,via,event_id,quote,said_at,created_at) '
                    'VALUES(:identifier,:org,:org_id,:source,:via,:event_id,:quote,:said_at,:created_at)'),
    'graph_names': ('SELECT * FROM graph_names WHERE node_id=?', 'DELETE FROM graph_names WHERE node_id=?',
                    'INSERT INTO graph_names(node_id,name,created_at) VALUES(:node_id,:name,:created_at)'),
    'org_edits': ('SELECT * FROM org_edits WHERE domain=?', 'DELETE FROM org_edits WHERE domain=?',
                  'INSERT INTO org_edits(domain,fields,updated_at) VALUES(:domain,:fields,:updated_at)'),
    'projects': ('SELECT org,value,close_date,notes,board_id,stage,stage_reason,stage_event_id,stage_by,stage_at,outcome,outcome_reason,'
                 'sort_run,sort_tries,sort_note FROM projects WHERE id=?', None,
                 'UPDATE projects SET org=:org,value=:value,close_date=:close_date,notes=:notes,board_id=:board_id,stage=:stage,'
                 'stage_reason=:stage_reason,stage_event_id=:stage_event_id,stage_by=:stage_by,stage_at=:stage_at,outcome=:outcome,'
                 'outcome_reason=:outcome_reason,sort_run=:sort_run,sort_tries=:sort_tries,sort_note=:sort_note WHERE id=:id'),
}


def _snapshot(db, table, key):
    row = db.execute(STATEMENTS[table][0], (key,)).fetchone()
    return dict(row) if row else None


def _restore(db, table, key, row):
    read, delete, write = STATEMENTS[table]
    if table == 'projects':  # only what an import can change goes back; the project itself stays
        if row:
            db.execute(write, {**{c: row.get(c) for c in PROJECT_COLUMNS}, 'id': key})
        return
    db.execute(delete, (key,))
    if row:
        db.execute(write, row)


def _person(db, rec, node_id, now, undo):
    fields = {k: rec[k] for k in ('title', 'location', 'linkedin_url', 'notes') if rec.get(k)}
    if rec.get('phone'):
        fields['phones'] = [rec['phone']]
    if not node_id:
        made = graph.create_record(db, 'person', {'name': rec['name'], 'email': rec.get('email'), 'org': rec.get('org')}, now)
        node_id = made['node_id']
        if made.get('existing'):
            return node_id, False
        undo['made'].append(node_id)
        # the graph has no node for it until the rebuild: its details go on its identity directly
        if fields:
            fields.pop('phones', None)
            if rec.get('phone') and graph.PHONE.fullmatch(rec['phone']):
                fields['phones'] = {'added': [rec['phone']], 'removed': []}
            db.execute('INSERT OR REPLACE INTO person_edits(identifier,fields,updated_at) VALUES(?,?,?)', (node_id, json.dumps(fields), now))
        return node_id, True
    row = graph.resolve(db, node_id)
    group = crm.group_of(row)
    undo['changed'].append({'kind': 'person', 'id': node_id, 'edits': {i: _snapshot(db, 'person_edits', i) for i in group},
                            'orgs': {i: _snapshot(db, 'person_orgs', i) for i in group},
                            'name': {i: _snapshot(db, 'graph_names', i) for i in group}})
    if fields:
        if 'phones' in fields:  # added to the numbers they have
            have = graph.shown_contacts(group, graph.person_edits_for(db, group))['phones']
            fields['phones'] = list(dict.fromkeys([p['value'] for p in have] + fields['phones']))
        graph.person_edit(db, node_id, fields, now)
    if rec.get('org'):
        info = json.loads(row['info'] or '{}')
        current = db.execute('SELECT name FROM graph_nodes WHERE id=?', (info.get('org'),)).fetchone() if info.get('org') else None
        if not current or current['name'].casefold() != rec['org'].casefold():
            graph.set_org(db, node_id, rec['org'], 'owner', now)
    return node_id, False


def _org(db, rec, node_id, now, undo):
    fields = {k: rec[k] for k in ('linkedin_url', 'industry', 'size', 'location', 'founded', 'about', 'notes') if rec.get(k)}
    for k, limit in graph.ORG_FIELDS.items():
        if k in fields:
            fields[k] = fields[k][:limit]
    if not node_id:
        made = graph.create_record(db, 'org', {'name': rec['name'], 'website': rec.get('website')}, now)
        node_id = made['node_id']
        if made.get('existing'):
            return node_id, False
        undo['made'].append(node_id)
        if fields:
            key = node_id if node_id.startswith('org:named:') else node_id[4:]
            db.execute('INSERT OR REPLACE INTO org_edits(domain,fields,updated_at) VALUES(?,?,?)', (key, json.dumps(fields), now))
        return node_id, True
    key = node_id if node_id.startswith('org:named:') else node_id[4:]
    undo['changed'].append({'kind': 'org', 'id': node_id, 'key': key, 'edits': _snapshot(db, 'org_edits', key)})
    if fields:
        graph.org_edit(db, node_id, fields, now)
    return node_id, False


def _project(db, rec, node_id, now, undo):
    args = {k: rec[k] for k in ('org', 'value', 'close_date', 'notes') if rec.get(k)}
    if rec.get('org') and not graph.ORG_DOMAIN.fullmatch(re.sub(r'^(?:https?://)?(?:www\.)?', '', rec['org'].lower()).split('/')[0]):
        if not _org_by_name(db, rec['org']):
            args.pop('org')  # an organization not in the Graph by that name: left out rather than the row
    if rec.get('board'):
        board = db.execute('SELECT id,stages,side FROM boards WHERE name=? COLLATE NOCASE', (rec['board'],)).fetchone()
        if board:
            args['board_id'] = board['id']
            if rec.get('stage'):
                names = {s.casefold(): s for s in graph.stage_names(board)}
                if rec['stage'].casefold() in names:
                    args['stage'] = names[rec['stage'].casefold()]
    elif rec.get('stage') and node_id:
        args['stage'] = rec['stage']
    if node_id:
        pid = int(node_id.split(':', 1)[1])
        undo['changed'].append({'kind': 'project', 'id': node_id, 'row': _snapshot(db, 'projects', pid),
                                'moves': db.execute('SELECT COALESCE(MAX(id),0) FROM project_moves WHERE project_id=?', (pid,)).fetchone()[0]})
        if 'stage' in args and 'board_id' not in args:
            row = db.execute('SELECT b.* FROM projects p JOIN boards b ON b.id=p.board_id WHERE p.id=?', (pid,)).fetchone()
            names = {s.casefold(): s for s in graph.stage_names(row)} if row else {}
            if args['stage'].casefold() in names:
                args['stage'] = names[args['stage'].casefold()]
            else:
                args.pop('stage')
        try:
            graph.save_project(db, {**args, 'node_id': node_id}, now)
        except ValueError:
            graph.save_project(db, {**{k: v for k, v in args.items() if k != 'org'}, 'node_id': node_id}, now)
        return node_id, False
    try:
        made = graph.save_project(db, {**args, 'name': rec['name']}, now)
    except ValueError:
        if 'org' not in args:
            raise
        made = graph.save_project(db, {**{k: v for k, v in args.items() if k != 'org'}, 'name': rec['name']}, now)
    undo['made'].append(made['node_id'])
    return made['node_id'], True


def run(db, kind, text, mapping, now, name=None):
    """Import the file: new rows made, matching ones updated. One transaction; the caller rebuilds the graph."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    _, _, _, planned = plan(db, kind, text, mapping)
    undo = {'made': [], 'changed': []}
    counts = {'new': 0, 'updated': 0, 'skipped': 0}
    step = {'person': _person, 'org': _org, 'project': _project}[kind]
    for p in planned:
        if p['status'] == 'skip':
            counts['skipped'] += 1
            continue
        try:
            _, made = step(db, p['record'], p.get('node_id'), now, undo)
        except ValueError:
            counts['skipped'] += 1
            continue
        counts['new' if made else 'updated'] += 1
    found = db.execute('INSERT INTO graph_imports(kind,name,counts,undo,at) VALUES(?,?,?,?,?)',
                       (kind, str(name or '')[:120] or None, json.dumps(counts), json.dumps(undo), now)).lastrowid
    db.execute('UPDATE graph_imports SET undo=NULL WHERE id<?', (found,))  # only the last import can be undone
    return {'import_id': found, 'kind': kind, **counts}


def last(db):
    """The last import that can still be undone, for "Undo last import"."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='graph_imports'").fetchone():
        return None
    row = db.execute('SELECT id,kind,name,counts,at FROM graph_imports WHERE undone_at IS NULL AND undo IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
    return {'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'at': row['at'], **json.loads(row['counts'] or '{}')} if row else None


def undo(db, import_id, now):
    """Undo the last import: what it made goes, what it changed goes back."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT * FROM graph_imports WHERE id=? AND undone_at IS NULL AND undo IS NOT NULL', (int(import_id or 0),)).fetchone()
    if not row:
        raise ValueError('Only the last import can be undone')
    plan_ = json.loads(row['undo'])
    for item in reversed(plan_['changed']):
        if item['kind'] == 'person':
            for table, key in (('person_edits', 'edits'), ('person_orgs', 'orgs'), ('graph_names', 'name')):
                for ident, snap in item[key].items():
                    _restore(db, table, ident, snap)
        elif item['kind'] == 'org':
            _restore(db, 'org_edits', item['key'], item['edits'])
        else:
            pid = int(item['id'].split(':', 1)[1])
            if item['row']:
                _restore(db, 'projects', pid, item['row'])
            db.execute('DELETE FROM project_moves WHERE project_id=? AND id>?', (pid, item['moves']))
    for node_id in plan_['made']:
        if node_id.startswith('project:'):
            try:
                graph.delete_project(db, node_id)
            except ValueError:
                pass
            continue
        db.execute('DELETE FROM manual_nodes WHERE identifier=?', (node_id,))
        db.execute('DELETE FROM graph_names WHERE node_id=?', (node_id,))
        db.execute('DELETE FROM person_edits WHERE identifier=?', (node_id,))
        db.execute('DELETE FROM person_orgs WHERE identifier=?', (node_id,))
        db.execute('DELETE FROM record_owners WHERE node_id=?', (node_id,))
        if node_id.startswith('org:'):
            db.execute('DELETE FROM org_edits WHERE domain=?', (node_id if node_id.startswith('org:named:') else node_id[4:],))
    db.execute('UPDATE graph_imports SET undone_at=?,undo=NULL WHERE id=?', (now, row['id']))
    return {'undone': row['id'], 'kind': row['kind'], **json.loads(row['counts'] or '{}')}
