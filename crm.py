"""The Graph as a CRM: who owns a record, one organization with several websites, and the lists filtered and sorted
on the Machine.

Owner: a person, organization or project belongs to someone in the owner's Work Fleet. Nobody picked means the
owner of this Machine: they made it, or their Machine found it. A pick is kept on the record (record_owners: the
person's identity it was saved on, so it stays through merges), shown on its page and its tile, and each change is a
line in its timeline (record_changes). The graph build puts it on the node (info.owner, a Fleet member id; none is
the owner here), so the lists filter on it in SQL.

Several websites: "Same organization as…" folds one organization into another (org_aliases). Every rebuild maps the
first one's people, and the people of every address at its domain, to the second; its notes join the second's, its
projects point to it, its domain becomes one more website of it, and enrichment looks up every one of its domains.
Split off undoes it and keeps the two apart. Likely doubles (the same name, the same company name on another top
level domain, the same LinkedIn page) are offered as "Same organization?" until merged or kept apart (org_apart).

Lists: People, Organizations and Projects filter by owner, board, stage, organization, value and expected close, and
sort by value and close date too. A person or organization matches through the open projects they are on."""
from __future__ import annotations

import datetime as dt
import json
import re

import enrichment
import graph

SCHEMA = '''
CREATE TABLE IF NOT EXISTS record_owners(node_id TEXT PRIMARY KEY,owner TEXT NOT NULL,name TEXT,set_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS record_changes(id INTEGER PRIMARY KEY,node_id TEXT NOT NULL,field TEXT NOT NULL,old TEXT,new TEXT,at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS record_changes_node ON record_changes(node_id,at);
CREATE TABLE IF NOT EXISTS org_aliases(alias TEXT PRIMARY KEY,target TEXT NOT NULL,undo TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_apart(a TEXT NOT NULL,b TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS graph_imports(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,name TEXT,counts TEXT,undo TEXT,at TEXT NOT NULL,undone_at TEXT);
'''
ME = 'me'
MEMBER = re.compile(r'[A-Za-z0-9_.:@-]{1,80}')
CHANGES = 30            # timeline lines per record


def ensure(db):
    import crm_fields
    db.executescript(SCHEMA)
    crm_fields.ensure(db)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ------------------------------------------------------------------ owner

def node_row(db, node_id):
    row = graph.resolve(db, str(node_id or ''))
    if not row or row['kind'] not in ('person', 'org', 'project'):
        raise ValueError('Pick a person, organization or project from your Graph')
    return row


def group_of(row):
    """The ids a record's own data can be kept under: a person's identities, else the node itself."""
    info = json.loads(row['info'] or '{}')
    return sorted(set(info.get('identifiers') or ()) | {row['id']})


def set_owner(db, node_id, owner, owner_name, now):
    """The owner picks who owns a record: 'me' or a Work Fleet member id (the caller checks it is one, and gives their
    name). The change is a line in the record's timeline."""
    row = node_row(db, node_id)
    owner = str(owner or ME)
    if owner != ME and not MEMBER.fullmatch(owner):
        raise ValueError('Pick an owner from your Work Fleet')
    info = json.loads(row['info'] or '{}')
    was, was_name = info.get('owner') or ME, info.get('owner_name')
    if was == owner:
        return {'node_id': row['id'], 'owner': owner, 'kept': True}
    group = group_of(row)
    db.execute(f"DELETE FROM record_owners WHERE node_id IN ({','.join('?' * len(group))})", group)
    db.execute('INSERT INTO record_owners(node_id,owner,name,set_at) VALUES(?,?,?,?)', (row['id'], owner, owner_name if owner != ME else None, now))
    change(db, row['id'], 'owner', json.dumps({'id': was, 'name': was_name}), json.dumps({'id': owner, 'name': owner_name if owner != ME else None}), now)
    # The graph is rebuilt by the caller; until then the node says it already
    info.update(owner=None if owner == ME else owner, owner_name=None if owner == ME else owner_name)
    db.execute('UPDATE graph_nodes SET info=? WHERE id=?', (json.dumps(info), row['id']))
    return {'node_id': row['id'], 'owner': owner, 'owner_name': owner_name if owner != ME else None}


def change(db, node_id, field, old, new, now):
    db.execute('INSERT INTO record_changes(node_id,field,old,new,at) VALUES(?,?,?,?,?)', (node_id, field, old, new, now))


def changes(db, row, limit=CHANGES):
    """A record's own history for its timeline: owner changes, organizations merged into it or split off, imports."""
    group = group_of(row)
    if row['kind'] == 'org':
        group += [r[0] for r in db.execute('SELECT alias FROM org_aliases WHERE target=?', (row['id'],))]
    out = []
    for r in db.execute(f"SELECT field,old,new,at FROM record_changes WHERE node_id IN ({','.join('?' * len(group))}) ORDER BY at DESC,id DESC LIMIT ?",
                        (*group, limit)):
        item = {'field': r['field'], 'at': r['at']}
        for key in ('old', 'new'):
            try:
                item[key] = json.loads(r[key]) if r[key] else None
            except ValueError:
                item[key] = r[key]
        out.append(item)
    return out


def owner_choices(fleets):
    """[{'id', 'name', 'fleet'}]: the members of the owner's Work Fleets, who can own a record (the owner is 'me')."""
    out, seen = [], set()
    for ref, found in (fleets or {}).items():
        if (found or {}).get('kind') != 'work':
            continue
        for m in found.get('members') or ():
            if not isinstance(m, dict) or m.get('me') or not m.get('member_id') or m['member_id'] in seen:
                continue
            if not MEMBER.fullmatch(str(m['member_id'])):
                continue
            seen.add(m['member_id'])
            name = ' '.join(str(m.get('name') or '').split())[:100] or next(iter(m.get('emails') or ()), '') or 'A teammate'
            out.append({'id': str(m['member_id']), 'name': name, 'fleet': str(found.get('name') or '')[:80]})
    return sorted(out, key=lambda m: m['name'].casefold())


# ------------------------------------------------------------------ the build: owners and organizations with several websites

def aliases(db):
    """{alias org id: the org id it is part of}, followed through chains."""
    rows = dict(db.execute('SELECT alias,target FROM org_aliases').fetchall())
    out = {}
    for alias in rows:
        seen, target = {alias}, rows[alias]
        while target in rows and target not in seen:
            seen.add(target)
            target = rows[target]
        if target != alias:
            out[alias] = target
    return out


def org_profile(db, org_id):
    """An organization's details as the build gives them (the owner's edits over the looked-up profile)."""
    key = org_id if org_id.startswith('org:named:') else org_id[4:]
    found = db.execute("SELECT profile FROM org_profiles WHERE domain=? AND profile IS NOT NULL AND status!='wrong'", (key,)).fetchone()
    edit = db.execute('SELECT fields FROM org_edits WHERE domain=?', (key,)).fetchone()
    base = {} if org_id.startswith('org:named:') else {'website': enrichment.website(key)}
    return {**base, **(json.loads(found['profile']) if found else {}), **(json.loads(edit['fields']) if edit else {})}


def decorate(db, nodes, links):
    """After the build made its nodes and links: fold organizations into the one they are part of, and put each
    record's owner on it. nodes: (id, kind, name, strength, last_at, signals, gap, quiet, trend, info JSON)."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='record_owners'").fetchone():
        return nodes, links  # an old database the build met before ensure
    folded = aliases(db)
    if folded:
        nodes, links = _fold(db, nodes, links, folded)
    owners = {r['node_id']: r for r in db.execute('SELECT node_id,owner,name,set_at FROM record_owners')}
    if owners:
        nodes = _owners(nodes, owners, folded)
    import crm_fields
    return crm_fields.decorate(db, nodes, folded), links


def _fold(db, nodes, links, folded):
    order = [n[0] for n in nodes]
    by_id = {n[0]: list(n) for n in nodes}
    moved = {}
    for alias, target in sorted(folded.items()):
        node = by_id.pop(alias, None)
        if node is None:  # a website nobody wrote from yet: one more of its websites all the same
            keep = by_id.get(target)
            if keep is not None and keep[1] == 'org' and not alias.startswith('org:named:'):
                mine = json.loads(keep[9])
                mine['domains'] = list(dict.fromkeys(d for d in [mine.get('domain'), *(mine.get('domains') or ()), alias[4:]] if d))
                mine['aliases'] = sorted({*(mine.get('aliases') or ()), alias})
                keep[9] = json.dumps(mine)
            continue
        if node[1] != 'org':
            by_id[alias] = node
            continue
        info = json.loads(node[9])
        keep = by_id.get(target)
        if keep is None:  # nobody of its own: the alias's node stands for it
            named = db.execute('SELECT name FROM graph_names WHERE node_id=?', (target,)).fetchone()
            made = db.execute('SELECT name FROM manual_nodes WHERE identifier=?', (target,)).fetchone()
            domain = None if target.startswith('org:named:') else target[4:]
            name = (named or made or [None])[0] or (enrichment.readable_name(domain) if domain else node[2])
            mine = {**info, 'domain': domain, 'profile': org_profile(db, target) or None, 'edited': [], 'manual': bool(made)}
            keep = [target, 'org', name[:200], 0.0, None, 0, None, 0, None, json.dumps(mine)]
            by_id[target] = keep
            order.append(target)
        mine = json.loads(keep[9])
        domains = [d for d in [mine.get('domain'), *(mine.get('domains') or ()), info.get('domain'), *(info.get('domains') or ())] if d]
        mine['domains'] = list(dict.fromkeys(domains))
        theirs = {k: v for k, v in (info.get('profile') or {}).items() if k not in ('website', 'name')}
        mine['profile'] = {**theirs, **(mine.get('profile') or {})} or None
        for key in ('role', 'role_by'):
            if not mine.get('role') and info.get('role'):
                mine[key] = info.get(key)
        mine['paying'] = bool(mine.get('paying') or info.get('paying'))
        mine['manual'] = bool(mine.get('manual') or info.get('manual'))
        mine['aliases'] = sorted({*(mine.get('aliases') or ()), alias})
        keep[3] = round(keep[3] + node[3], 4)
        keep[4] = max(filter(None, (keep[4], node[4])), default=None)
        keep[5] = keep[5] + node[5]
        keep[9] = json.dumps(mine)
        moved[alias] = target
    if not moved:
        return [tuple(by_id[i]) for i in dict.fromkeys(order) if i in by_id], links
    members = set()
    out_links = []
    for a, b, kind, strength, last in links:
        if kind == 'member' and b in moved:
            b = moved[b]
            members.add(a)
        out_links.append((moved.get(a, a), b, kind, strength, last))
    for node_id in members:
        row = by_id.get(node_id)
        if row and row[1] == 'person':
            info = json.loads(row[9])
            if info.get('org') in moved:
                info['org'] = moved[info['org']]
                row[9] = json.dumps(info)
    for node_id, row in by_id.items():  # someone whose stated organization is the alias (and nobody else in it)
        if row[1] == 'person' and node_id not in members and any('"' + a + '"' in row[9] for a in moved):
            info = json.loads(row[9])
            if info.get('org') in moved:
                info['org'] = moved[info['org']]
                row[9] = json.dumps(info)
    return [tuple(by_id[i]) for i in dict.fromkeys(order) if i in by_id], out_links


def _owners(nodes, owners, folded):
    keys = [k for k in owners]
    out = []
    for n in nodes:
        if n[1] == 'person':
            if not any('"' + k + '"' in n[9] for k in keys) and n[0] not in owners:
                out.append(n)
                continue
            info = json.loads(n[9])
            group = set(info.get('identifiers') or ()) | {n[0]}
            found = max((owners[k] for k in group if k in owners), key=lambda r: r['set_at'], default=None)
        else:
            info = None
            here = [owners[k] for k in [n[0], *(a for a, t in folded.items() if t == n[0])] if k in owners]
            found = max(here, key=lambda r: r['set_at'], default=None)
        if not found:
            out.append(n)
            continue
        info = info or json.loads(n[9])
        mine = found['owner'] == ME
        info.update(owner=None if mine else found['owner'], owner_name=None if mine else found['name'])
        out.append(tuple(n[:9]) + (json.dumps(info),))
    return out


# ------------------------------------------------------------------ organizations with several websites

def canonical(db, org_id):
    """The organization an org id is part of now (itself when it isn't folded into another)."""
    return aliases(db).get(org_id, org_id)


def canonical_domain(db, domain):
    """A website's organization's own domain: salesflare.io is salesflare.com once they're one."""
    if not domain:
        return domain
    target = canonical(db, 'org:' + domain)
    return target[4:] if target.startswith('org:') and not target.startswith('org:named:') else domain


def _org(db, org_id):
    row = db.execute("SELECT id,name,info FROM graph_nodes WHERE id=? AND kind='org'", (org_id,)).fetchone()
    return row


def merge_orgs(db, node_id, into=None, website=None, now=None):
    """"Same organization as…": node_id is part of into (another organization in the Graph), or website is one more of
    node_id's websites. The one with a website stays when only one has one (it keeps the name the owner picked).
    Its people, projects and notes go along; Split off undoes it."""
    now = now or now_iso()
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    here = _org(db, canonical(db, str(node_id or '')))
    if not here:
        raise ValueError('Pick an organization from your Graph')
    if website:
        domain = graph.website_domain(website)
        if not domain:
            raise ValueError(f'{website} is not a website, like acme.com')
        alias, target = 'org:' + domain, here['id']
        if canonical(db, alias) == target or alias == target:
            raise ValueError(f'{domain} is one of its websites already')
        if domain in graph.FREEMAIL:
            raise ValueError(f'{domain} is free email: many companies use it')
        other = _org(db, canonical(db, alias))
        if other and other['id'] != target:
            alias = other['id']  # that website is an organization of its own: it joins this one
    else:
        other = _org(db, canonical(db, str(into or '')))
        if not other:
            raise ValueError('Pick the organization it is the same as')
        alias, target = here['id'], other['id']
        if alias == target:
            raise ValueError('That is the same organization already')
    name_kept = None
    if alias.startswith('org:') and not alias.startswith('org:named:') and target.startswith('org:named:'):
        # Only the first one has a website: it stays, with the name of the one picked
        picked = _org(db, target)
        alias, target = target, alias
        name_kept = picked['name'] if picked else None
    undo = {'projects': [], 'edits': None, 'name': None, 'apart': None}
    alias_domain = None if alias.startswith('org:named:') else alias[4:]
    target_domain = None if target.startswith('org:named:') else target[4:]
    if alias_domain and target_domain:
        ids = [r[0] for r in db.execute('SELECT id FROM projects WHERE org=?', (alias_domain,))]
        db.execute('UPDATE projects SET org=? WHERE org=?', (target_domain, alias_domain))
        undo['projects'] = ids
    key = lambda org_id: org_id if org_id.startswith('org:named:') else org_id[4:]
    mine = db.execute('SELECT fields FROM org_edits WHERE domain=?', (key(target),)).fetchone()
    theirs = db.execute('SELECT fields FROM org_edits WHERE domain=?', (key(alias),)).fetchone()
    if theirs:
        kept, added = json.loads(mine['fields']) if mine else {}, json.loads(theirs['fields'])
        undo['edits'] = mine['fields'] if mine else ''
        merged = {**{k: v for k, v in added.items() if k != 'notes'}, **kept}
        notes = [n for n in (kept.get('notes'), added.get('notes')) if n]
        if notes:
            merged['notes'] = '\n\n'.join(dict.fromkeys(notes))[:4000]
        db.execute('INSERT OR REPLACE INTO org_edits(domain,fields,updated_at) VALUES(?,?,?)', (key(target), json.dumps(merged), now))
    if name_kept:
        was = db.execute('SELECT name FROM graph_names WHERE node_id=?', (target,)).fetchone()
        undo['name'] = was['name'] if was else ''
        db.execute('INSERT OR REPLACE INTO graph_names(node_id,name,created_at) VALUES(?,?,?)', (target, name_kept, now))
    pair = tuple(sorted((alias, target)))
    if db.execute('DELETE FROM org_apart WHERE a=? AND b=?', pair).rowcount:
        undo['apart'] = True
    db.execute('INSERT OR REPLACE INTO org_aliases(alias,target,undo,created_at) VALUES(?,?,?,?)', (alias, target, json.dumps(undo), now))
    db.execute('UPDATE org_aliases SET target=? WHERE target=?', (target, alias))  # what was part of it is part of the new one
    shown = (here['name'] if here['id'] == alias else other['name'] if other else None) or alias_domain or alias
    change(db, target, 'merged', None, json.dumps({'id': alias, 'name': shown}), now)
    return {'node_id': target, 'alias': alias, 'merged': shown}


def split_org(db, alias, now=None):
    """Split off: a website or organization folded into another stands on its own again, and isn't offered as the same."""
    now = now or now_iso()
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT * FROM org_aliases WHERE alias=?', (str(alias or ''),)).fetchone()
    if not row:
        raise ValueError('That is not part of another organization')
    undo = json.loads(row['undo'] or '{}')
    alias, target = row['alias'], row['target']
    alias_domain = None if alias.startswith('org:named:') else alias[4:]
    target_domain = None if target.startswith('org:named:') else target[4:]
    if alias_domain and target_domain and undo.get('projects'):
        marks = ','.join('?' * len(undo['projects']))
        db.execute(f'UPDATE projects SET org=? WHERE org=? AND id IN ({marks})', (alias_domain, target_domain, *undo['projects']))
    key = target if target.startswith('org:named:') else target[4:]
    if undo.get('edits') is not None:
        if undo['edits']:
            db.execute('INSERT OR REPLACE INTO org_edits(domain,fields,updated_at) VALUES(?,?,?)', (key, undo['edits'], now))
        else:
            db.execute('DELETE FROM org_edits WHERE domain=?', (key,))
    if undo.get('name') is not None:
        if undo['name']:
            db.execute('INSERT OR REPLACE INTO graph_names(node_id,name,created_at) VALUES(?,?,?)', (target, undo['name'], now))
        else:
            db.execute('DELETE FROM graph_names WHERE node_id=?', (target,))
    db.execute('DELETE FROM org_aliases WHERE alias=?', (alias,))
    db.execute('INSERT OR IGNORE INTO org_apart(a,b,created_at) VALUES(?,?,?)', (*sorted((alias, target)), now))
    change(db, target, 'split', None, json.dumps({'id': alias, 'name': alias_domain or alias}), now)
    return {'node_id': target, 'split': alias}


def keep_apart(db, a, b, now=None):
    """Not the same organization: the pair isn't offered again."""
    a, b = sorted((str(a or ''), str(b or '')))
    if a == b or not (a.startswith('org:') and b.startswith('org:')):
        raise ValueError('Pick two organizations')
    db.execute('INSERT OR IGNORE INTO org_apart(a,b,created_at) VALUES(?,?,?)', (a, b, now or now_iso()))
    return {'apart': [a, b]}


SHORT_LABELS = {'mail', 'email', 'info', 'team', 'app', 'apps', 'get', 'try', 'use', 'my', 'go', 'the', 'web', 'home', 'news', 'shop', 'cloud', 'online'}


def _label(domain):
    """The company part of a domain: salesflare for salesflare.com, app.salesflare.io and salesflare.co.uk."""
    try:
        reg = enrichment.registrable(domain)
    except Exception:  # noqa: BLE001 - a domain the helper can't read is just not compared
        reg = domain
    first = str(reg or '').split('.', 1)[0]
    return first if len(first) >= 4 and first not in SHORT_LABELS else None


def org_doubles(db, limit=30, node_id=None):
    """Same organization? Likely doubles among the organizations: the same name, the same company name on another
    domain (acme.com and acme.io), or the same LinkedIn page. Kept-apart pairs never come back. node_id: only the
    ones for this organization."""
    rows = db.execute("SELECT id,name,strength,info FROM graph_nodes WHERE kind='org'").fetchall()
    if len(rows) < 2:
        return []
    apart = {(r['a'], r['b']) for r in db.execute('SELECT a,b FROM org_apart')}
    groups = {}
    for r in rows:
        info = json.loads(r['info'] or '{}')
        keys = []
        name = graph.folded_words(r['name'])
        if name and len(name) >= 3:
            keys.append(('name', name))
        for d in info.get('domains') or [info.get('domain')]:
            label = _label(d) if d else None
            if label:
                keys.append(('label', label))
        link = str((info.get('profile') or {}).get('linkedin_url') or '').lower().rstrip('/').split('?')[0]
        link = re.sub(r'^https?://([a-z]{2,3}\.)?', '', link)
        if re.fullmatch(r'linkedin\.com/company/[^/]+', link):
            keys.append(('linkedin', link))
        for k in set(keys):
            groups.setdefault(k, []).append(r)
    pairs = {}
    why_text = {'name': 'Same name', 'linkedin': 'Same LinkedIn page', 'label': 'Same company name on another website'}
    for (kind, _), members in groups.items():
        if len(members) < 2 or len(members) > 6:
            continue  # a very common name says little
        for i, x in enumerate(members):
            for y in members[i + 1:]:
                pair = tuple(sorted((x['id'], y['id'])))
                if pair in apart or (node_id and node_id not in pair):
                    continue
                score = (3 if kind == 'linkedin' else 2 if kind == 'name' else 1) + min(x['strength'], y['strength']) / 10
                if pair not in pairs or pairs[pair][0] < score:
                    pairs[pair] = (score, why_text[kind], x if x['id'] == pair[0] else y, y if y['id'] == pair[1] else x)
    out = []
    for (a, b), (score, why, x, y) in sorted(pairs.items(), key=lambda p: -p[1][0])[:limit]:
        x, y = sorted((x, y), key=lambda r: (-r['strength'], r['id']))  # the closer one first: the other joins it
        out.append({'a': _brief(x), 'b': _brief(y), 'why': why})
    return out


def _brief(row):
    info = json.loads(row['info'] or '{}')
    return {'id': row['id'], 'kind': 'org', 'name': row['name'], 'domain': info.get('domain'), 'domains': info.get('domains') or [],
            'profile': {k: v for k, v in (info.get('profile') or {}).items() if k in ('logo', 'industry', 'website')}}


# ------------------------------------------------------------------ lists: filters and sorts on the Machine

FILTERS = ('owner', 'tag', 'board', 'stage', 'org', 'value_min', 'value_max', 'close_from', 'close_to')
SORTS = ('value', 'close')


def clean_filters(raw):
    """The filters a list asks for, checked: {name: value}; unknown and empty ones left out."""
    out = {}
    for key in FILTERS:
        value = str((raw or {}).get(key) or '').strip()
        if not value:
            continue
        if key == 'owner':
            if value != ME and not MEMBER.fullmatch(value):
                raise ValueError('Owner: you or someone in your Work Fleet')
        elif key == 'tag':
            import crm_fields
            value = crm_fields.clean_tag(value)
        elif key == 'board':
            if not value.isdigit():
                raise ValueError('Board: one of your boards')
            value = int(value)
        elif key in ('value_min', 'value_max'):
            try:
                value = float(value.replace(',', '').replace(' ', '').lstrip('€$£'))
            except ValueError:
                raise ValueError('Value: a number, like 12000') from None
        elif key in ('close_from', 'close_to'):
            try:
                value = dt.date.fromisoformat(value).isoformat()
            except ValueError:
                raise ValueError('Close date: a date, like 2026-12-15') from None
        elif len(value) > 200:
            raise ValueError(f'{key.capitalize()}: up to 200 characters')
        out[key] = value
    return out


def org_ids(db, said):
    """The organizations a filter names: an org id, a website or a name."""
    said = str(said or '').strip()
    if said.startswith('org:'):
        return [canonical(db, said)]
    domain = graph.website_domain(said) if '.' in said and ' ' not in said else None
    if domain:
        return [canonical(db, 'org:' + domain)]
    return [r[0] for r in db.execute("SELECT id FROM graph_nodes WHERE kind='org' AND name=? COLLATE NOCASE", (said,))]


def kind_of(name):
    return 'project' if name.startswith('projects') else 'org' if name == 'orgs' else 'person'


def _project_where(filters, closed_too):
    where, args = ([] if closed_too else ['p.closed_at IS NULL']), []
    if 'board' in filters:
        where.append('p.board_id=?')
        args.append(filters['board'])
    if 'stage' in filters:
        where.append('p.stage=? COLLATE NOCASE')
        args.append(filters['stage'])
    if 'value_min' in filters:
        where.append('p.value>=?')
        args.append(filters['value_min'])
    if 'value_max' in filters:
        where.append('p.value<=?')
        args.append(filters['value_max'])
    if 'close_from' in filters:
        where.append('p.close_date>=?')
        args.append(filters['close_from'])
    if 'close_to' in filters:
        where.append('p.close_date<=?')
        args.append(filters['close_to'])
    return ' AND '.join(where) or '1', args


def page_rows(db, name, filters, sort, limit, offset):
    """A page of a Graph list with filters or in a CRM order: (rows, how many match). The query is made of graph's
    fixed fragments for the list and list_query's for the filters; every value goes as a parameter."""
    where, args = graph._paged_where(db, name)
    extra_where, extra_args, extra_order = list_query(db, name, filters, sort)
    order = extra_order or graph.SORTS[sort]
    if extra_where:
        where, args = '(' + where + ') AND ' + extra_where, (*args, *extra_args)
    rows = db.execute('SELECT * FROM graph_nodes WHERE ' + where + ' ORDER BY ' + order + ',id LIMIT ? OFFSET ?', (*args, limit, offset)).fetchall()
    total = db.execute('SELECT COUNT(*) FROM graph_nodes WHERE ' + where, args).fetchone()[0] if extra_where else None
    return rows, total


def list_query(db, name, filters, sort):
    """(where, args, order) to add to a Graph list's query, or (None, (), None) when nothing is asked. Facts about
    projects (board, stage, value, close date, and the value and close sorts) go through a temporary table of what
    matches, so a long list still takes one pass."""
    kind = kind_of(name)
    facts = any(k in filters for k in ('board', 'stage', 'value_min', 'value_max', 'close_from', 'close_to'))
    if not filters and sort not in SORTS:
        return None, (), None
    where, args = [], []
    if 'owner' in filters:
        if filters['owner'] == ME:
            where.append("json_extract(info,'$.owner') IS NULL")
        else:
            where.append("json_extract(info,'$.owner')=?")
            args.append(filters['owner'])
    if 'tag' in filters:
        where.append("EXISTS(SELECT 1 FROM json_each(graph_nodes.info,'$.tags') t WHERE t.value=? COLLATE NOCASE)")
        args.append(filters['tag'])
    if 'org' in filters:
        ids = org_ids(db, filters['org'])
        marks = ','.join('?' * len(ids)) or "''"
        if kind == 'person':
            where.append(f"json_extract(info,'$.org') IN ({marks})")
            args += ids
        elif kind == 'org':
            where.append(f'id IN ({marks})')
            args += ids
        else:
            domains = [i[4:] for i in ids if not i.startswith('org:named:')]
            where.append(f"(id IN (SELECT 'project:'||p.id FROM projects p WHERE p.org IN ({','.join('?' * len(domains)) or chr(39) * 2})) "
                         f"OR id IN (SELECT l.b FROM graph_links l JOIN graph_nodes m ON m.id=l.a WHERE l.kind='project' AND json_extract(m.info,'$.org') IN ({marks})))")
            args += domains + ids
    if facts or sort in SORTS:
        pw, pargs = _project_where(filters, closed_too=kind == 'project')
        db.execute('CREATE TEMP TABLE IF NOT EXISTS crm_pick(id TEXT PRIMARY KEY,value REAL,close TEXT)')
        db.execute('DELETE FROM temp.crm_pick')
        if kind == 'project':
            db.execute(f"INSERT OR REPLACE INTO temp.crm_pick SELECT 'project:'||p.id,p.value,p.close_date FROM projects p WHERE {pw}", pargs)
        elif kind == 'person':
            db.execute("INSERT OR REPLACE INTO temp.crm_pick SELECT x.n,SUM(p.value),MIN(p.close_date) FROM ("
                       "SELECT l.a n,CAST(substr(l.b,9) AS INTEGER) pid FROM graph_links l WHERE l.kind='project' "
                       f"UNION SELECT node_id,project_id FROM project_people) x JOIN projects p ON p.id=x.pid WHERE {pw} GROUP BY x.n", pargs)
        else:
            db.execute("INSERT OR REPLACE INTO temp.crm_pick SELECT y.o,SUM(p.value),MIN(p.close_date) FROM (SELECT DISTINCT o,pid FROM ("
                       "SELECT 'org:'||p.org o,p.id pid FROM projects p WHERE p.org IS NOT NULL "
                       "UNION SELECT json_extract(m.info,'$.org'),CAST(substr(l.b,9) AS INTEGER) FROM graph_links l JOIN graph_nodes m ON m.id=l.a "
                       f"WHERE l.kind='project' AND json_extract(m.info,'$.org') IS NOT NULL)) y JOIN projects p ON p.id=y.pid WHERE {pw} GROUP BY y.o", pargs)
            for alias, target in aliases(db).items():  # a project still pointing at a website that is part of another organization
                db.execute('INSERT OR IGNORE INTO temp.crm_pick SELECT ?,value,close FROM temp.crm_pick WHERE id=?', (target, alias))
        if facts:
            where.append('id IN (SELECT id FROM temp.crm_pick)')
    order = None
    if sort == 'value':
        pick = '(SELECT value FROM temp.crm_pick WHERE crm_pick.id=graph_nodes.id)'
        order = f'{pick} IS NULL,{pick} DESC,strength DESC'
    elif sort == 'close':
        pick = '(SELECT close FROM temp.crm_pick WHERE crm_pick.id=graph_nodes.id)'
        order = f'{pick} IS NULL,{pick},strength DESC'
    return ' AND '.join(where) or None, tuple(args), order
