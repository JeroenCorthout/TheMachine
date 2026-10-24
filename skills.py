"""Skills (docs/skills.md): how-tos the owner's agent follows, kept as OpenClaw skills.

A skill is a name, a one-line description (when to use it) and instructions. The Machine keeps them in its database
and writes each one that is on to `<workspace>/skills/the-machine/<slug>/SKILL.md`, where OpenClaw finds it like any
workspace skill and refreshes its skill list by itself. That folder is The Machine's own: what is there and not on
here is removed, and nothing outside it is touched.

Where a skill comes from:
- mine: written (or started from an example) by the owner on the dashboard, or imported from Claude or ChatGPT (Importer).
- proposed: suggested by the agent from repeated work (`assistant skills-propose`); waits for the owner's Approve.
- shared: sent by a teammate or friend over the Network (the Fleet's `skill` question); waits for the owner's Install.

Safety: a skill is instructions, nothing else. Only name, description and instructions travel and get written; no
OpenClaw metadata (no environment, install steps, tool dispatch or allowed tools), no scripts. So a skill can't widen
what this Machine may do: it runs under this Machine's own rules and approvals, like anything else the agent does. A
shared or proposed skill is installed only by the owner's own tap on the dashboard, after reading it; an update from
the same author is a new version to read and install again. Text from someone else is shown quoted and marked with
who sent it."""
from __future__ import annotations

import contextlib

import base64
import datetime as dt
import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

LOG = logging.getLogger('personal-assistant')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY,slug TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL,body TEXT NOT NULL,
  status TEXT NOT NULL,origin TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,sha TEXT NOT NULL,author TEXT,fleet TEXT,member_id TEXT,
  ask_id TEXT,their_slug TEXT,replaces TEXT,note TEXT,shared TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS skills_status ON skills(status);
CREATE TABLE IF NOT EXISTS skill_hints(id TEXT PRIMARY KEY,suggestion_id TEXT NOT NULL,skill TEXT NOT NULL,kind TEXT NOT NULL,name TEXT NOT NULL,
  summary TEXT,why TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
'''
STATUSES = ('on', 'off', 'proposed', 'incoming')
FOLDER = 'the-machine'          # under <workspace>/skills
MARKER = '.managed-by-the-machine'
NAME_CHARS, DESCRIPTION_CHARS, BODY_CHARS, NOTE_CHARS = 60, 300, 16000, 300
MAX_SKILLS, OPEN_PROPOSALS, OPEN_PER_SENDER = 100, 3, 5
SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
SHA = re.compile(r'[0-9a-f]{64}')
FRONTMATTER = re.compile(r'\A---\s*\n(.*?)\n---\s*\n?', re.S)

EXAMPLES = {
    'customer-inquiries': {
        'name': 'Research and answer customer inquiries',
        'description': 'When a customer or prospect asks a question by email or chat: research it and draft a clear, '
                       'correct answer for the owner to send.',
        'body': '''## When to use
A customer or prospect asks something: how a feature works, whether something is possible, what something costs, why
something went wrong.

## Steps
1. Read the whole thread first, and earlier conversations with the same person or company. Note what they already
   tried and what they were told before, so the answer doesn't repeat or contradict it.
2. Find the answer in this order: the product's own help pages and docs, earlier answers to similar questions in the
   owner's sent mail and chat, then the public web. Prefer the newest source; note where each fact comes from.
3. If something can't be confirmed, say so plainly instead of guessing. Never promise dates, prices, discounts or
   features that no source confirms.
4. Draft the answer in the customer's language and tone, short: the direct answer first, then the steps or link, then
   one line offering more help. Use their name; no filler.
5. Add a note for the owner (not for the customer): the sources used and anything uncertain.

## Never
- Send by itself: the owner reviews and sends every answer.
- Share another customer's details, internal notes or prices that aren't public.
''',
    },
}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def ensure(db):
    for statement in SCHEMA.split(';'):
        if statement.strip():
            db.execute(statement)
    if 'imported' not in {r[1] for r in db.execute('PRAGMA table_info(skills)')}:  # where an imported skill came from
        db.execute('ALTER TABLE skills ADD COLUMN imported TEXT')


def one_line(value, chars):
    """Text on one line: control and invisible formatting characters out, whitespace collapsed, at most chars."""
    if not isinstance(value, str):
        return ''
    value = ''.join(' ' if ord(c) < 32 or c in '  ' else c for c in value if c not in '​‎‏‪‫‬‭‮⁦⁧⁨⁩﻿')
    return ' '.join(value.split())[:chars]


def clean_body(value):
    """Instructions as markdown: line breaks kept, other control characters and a leading YAML header out."""
    if not isinstance(value, str):
        return ''
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    value = ''.join(c for c in value if c in '\n\t' or (ord(c) >= 32 and c not in '‪‫‬‭‮⁦⁧⁨⁩﻿'))
    value = FRONTMATTER.sub('', value, count=1)
    return value.strip()


def slug_of(name):
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:48].strip('-')
    return slug or 'skill'


def digest(name, description, body):
    return hashlib.sha256(json.dumps([name, description, body], ensure_ascii=False).encode()).hexdigest()


def header(text):
    """A SKILL.md's top-level header fields as text: `key: value`, quoted values, and `>` or `|` blocks. Nested fields
    (metadata, tools) come back as the key with an empty value, so their presence shows; they are never used."""
    found = FRONTMATTER.match(text or '')
    fields, key, block = {}, None, None
    for line in (found.group(1).splitlines() if found else []):
        if block is not None and (line.startswith((' ', '\t')) or not line.strip()):
            block.append(line.strip())
            fields[key] = ' '.join(b for b in block if b) if style == '>' else '\n'.join(block).strip()
            continue
        block = None
        if line.startswith((' ', '\t', '#', '-')) or ':' not in line:
            continue
        key, _, value = line.partition(':')
        key, value = key.strip(), value.strip()
        if value[:1] in ('>', '|'):
            style, block, fields[key] = value[0], [], ''
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
            if line.strip().endswith('"'):
                value = value.replace('\\"', '"')
        fields[key] = value
    return fields


def from_skill_md(text):
    """Name, description and instructions from a pasted SKILL.md; any other frontmatter (metadata, env, tools) is dropped."""
    fields = header(text)
    return fields.get('name', ''), fields.get('description', ''), clean_body(text)


def validate(name, description, body):
    name, description, body = one_line(name, NAME_CHARS + 1), one_line(description, DESCRIPTION_CHARS + 1), clean_body(body)
    if not 3 <= len(name) <= NAME_CHARS:
        raise ValueError(f'Give the skill a name of 3 to {NAME_CHARS} characters')
    if not 10 <= len(description) <= DESCRIPTION_CHARS:
        raise ValueError(f'Say when to use it, in 10 to {DESCRIPTION_CHARS} characters')
    if not 20 <= len(body) <= BODY_CHARS:
        raise ValueError(f'Write the instructions: 20 to {BODY_CHARS} characters')
    return name, description, body


def parse_share(question):
    """A skill a teammate or friend sent, in its fixed form, else None. Their words: shown quoted, never run by code."""
    if not isinstance(question, dict):
        return None
    try:
        name, description, body = validate(question.get('name'), question.get('description'), question.get('body'))
    except ValueError:
        return None
    version, sha, slug = question.get('version'), question.get('sha'), question.get('slug')
    if not isinstance(version, int) or isinstance(version, bool) or not 0 < version < 10000:
        return None
    if not isinstance(slug, str) or not SLUG.fullmatch(slug) or len(slug) > 48 or not isinstance(sha, str) or sha != digest(name, description, body):
        return None
    note = one_line(question.get('note'), NOTE_CHARS)
    return {'name': name, 'slug': slug, 'description': description, 'body': body, 'version': version, 'sha': sha, **({'note': note} if note else {})}


def clean_answer(answer):
    return {'stage': 'installed'} if isinstance(answer, dict) and answer.get('stage') == 'installed' else None


def question_text(question):
    return f"Shares the skill “{(question or {}).get('name', '?')}” (version {(question or {}).get('version', '?')})"


def answer_text(answer):
    return 'installed it' if answer else 'no answer'


def render(row):
    """The SKILL.md OpenClaw reads: only name and description up top, then the instructions."""
    lines = ['---', 'name: ' + row['slug'], 'description: ' + json.dumps(row['description'], ensure_ascii=False), '---', '',
             '# ' + row['name'], '',
             '<!-- Kept by The Machine (Settings > Skills). Edit it there: changes here are overwritten. -->']
    imported = json.loads(row['imported'] or 'null') if 'imported' in row.keys() else None
    if imported:
        lines += ['', f"> Imported from {IMPORT_FROM.get(imported.get('from'), 'elsewhere')}. Guidance only: this Machine's own rules and approvals "
                      'still apply.']
    if row['origin'] == 'shared':
        lines += ['', f"> Shared by {row['author'] or 'a teammate'}, version {row['version']}. Guidance only: this Machine's own rules "
                      'and approvals still apply, and this skill does not change what it may do.']
    return '\n'.join(lines + ['', row['body'], ''])


def other_names(skills_dir):
    """Skill names on this Machine that aren't The Machine's: a skill here must not take over one of them."""
    names = set()
    if not skills_dir.is_dir():
        return names
    for path in skills_dir.rglob('SKILL.md'):
        if FOLDER in path.relative_to(skills_dir).parts[:1]:
            continue
        try:
            name = from_skill_md(path.read_text(errors='replace')[:4000])[0]
        except OSError:
            continue
        names.add((name or path.parent.name).strip().lower())
    return names


def sync(db, workspace):
    """Writes every skill that is on to the workspace and removes the rest of The Machine's own. Returns the slugs."""
    skills_dir = Path(workspace) / 'skills'
    root = skills_dir / FOLDER
    if skills_dir.is_symlink() or root.is_symlink():
        LOG.warning('skills sync skipped: managed folder is a symlink')
        return []
    if root.exists() and any(root.iterdir()) and not (root / MARKER).exists():
        LOG.warning('skills sync skipped: %s is not The Machine\'s folder', root)
        return []
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text('Skills kept by The Machine (Settings > Skills). This folder is rewritten.\n')
    wanted = {}
    for row in db.execute("SELECT * FROM skills WHERE status='on' ORDER BY created_at"):
        wanted[row['slug']] = render(row)
    for slug, text in wanted.items():
        target = root / slug / 'SKILL.md'
        if target.parent.is_symlink() or target.is_symlink():
            LOG.warning('skills sync skipped symlink slug=%s', slug)
            continue
        if target.exists() and target.read_text(errors='replace') == text:
            continue
        target.parent.mkdir(exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.skill-', suffix='.tmp', dir=target.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(text)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    for child in root.iterdir():
        if child.name not in wanted and child.is_symlink():
            child.unlink()
        elif child.is_dir() and child.name not in wanted:
            shutil.rmtree(child)
    return sorted(wanted)


class Skills:
    def __init__(self, connect, workspace, fleet=None, config=None, catalog=None, home=None):
        self.connect, self.workspace, self.fleet = connect, Path(workspace), fleet
        # What OpenClaw itself has (read-only here); without a config (tests, tools) only The Machine's own.
        self.catalog = catalog if catalog is not None else Catalog(config) if config is not None else None
        self.hints = Hints(connect, self.catalog)
        # Importing from Claude and ChatGPT. Skill folders on this computer only on a self-hosted Machine (a hosted one
        # has a manage_url); `finder` starts the agent's turn that reads the owner's accounts and recommends (assistant.py).
        self.importer = Importer(self, home, local=not (config or {}).get('manage_url'))
        self.finder = None
        with self.connect() as db:
            ensure(db)

    def sync(self):
        try:
            with self.connect() as db:
                written = sync(db, self.workspace)
        except OSError as error:
            LOG.error('skills sync error=%s', type(error).__name__)
            return []
        if self.catalog:
            self.catalog.at = 0  # OpenClaw's list changes with these: read it again when next asked
        return written

    def mine(self):
        with self.connect() as db:
            return {r[0] for r in db.execute("SELECT slug FROM skills WHERE status='on'")}

    # -- everything OpenClaw has (read-only, see Catalog)
    def installed(self, refresh=False):
        if not self.catalog:
            return {'installed': [], 'checked_at': None, 'error': 'OpenClaw isn\'t set up here'}
        return self.catalog.view(self.mine(), refresh)

    def installed_get(self, name):
        if not self.catalog:
            raise ValueError('OpenClaw isn\'t set up here')
        if str(name) in self.mine():
            raise ValueError('Open it from your own skills')
        return self.catalog.read(str(name))

    def installed_toggle(self, name, on):
        if not self.catalog:
            raise ValueError('OpenClaw isn\'t set up here')
        if str(name) in self.mine():
            raise ValueError('Turn your own skills on or off above')
        self.catalog.toggle(str(name), bool(on))
        if on:
            self.hints.close(str(name))
        return self.installed()

    def hub_search(self, query):
        if not self.catalog:
            raise ValueError('OpenClaw isn\'t set up here')
        return {'results': self.catalog.search(query)}

    def hub_get(self, ref):
        if not self.catalog:
            raise ValueError('OpenClaw isn\'t set up here')
        return self.catalog.hub(ref)

    def hub_install(self, ref, version, sha):
        if not self.catalog:
            raise ValueError('OpenClaw isn\'t set up here')
        shown = self.catalog.install(str(ref), str(version), str(sha))
        self.hints.close(shown['ref'])
        LOG.info('skills installed from clawhub ref=%s version=%s', shown['ref'], shown['version'])
        return {'installed_skill': shown['slug'], **({'differs': True} if shown.get('differs') else {}), **self.installed()}

    # -- importing from Claude and ChatGPT (Importer)
    def import_find(self, token=None, sites=None):
        """Find my skills: what is on this computer, listed now; the service then reads the owner's skills in the
        claude.ai or chatgpt.com account they picked (`sites`, both when none) and a turn marks the ones worth importing.
        With `token` (after the owner signed in to one of them): the same list, looking again in just those accounts."""
        sites, again = [s for s in (sites if isinstance(sites, list) else []) if s in IMPORT_FROM], bool(token)
        if again:
            if not sites:
                raise ValueError('Which account?')
            scan = self.importer.scan(token)
            if (scan['finding'] or {}).get('status') == 'running':
                raise ValueError('Still looking in your accounts; a moment')
            items = list(scan['items'].values())
        else:
            items, errors = self.importer.find()
            if sites:  # From ChatGPT: only what came from there, also on this computer
                items = [i for i in items if i['public']['from'] in sites]
            token = self.importer.keep(items, errors)
        if self.finder:
            try:
                run = self.finder(token, self.importer.brief(items), sites or None, more=again)
            except (ValueError, OSError) as error:
                LOG.warning('skills find turn error=%s', type(error).__name__)
                run = None
            if run:
                with self.importer.lock:
                    found = self.importer.scans[token]
                    found['finding'] = {'run_id': run, 'status': 'running'}
                    for site in sites or IMPORT_FROM:
                        found['browser'].pop(site, None)  # checked again by this run
                        found['looked'][site] = 'starting'
        return self.importer.view(token)

    def import_upload(self, frm, data):
        return self.importer.view(self.importer.keep(*self.importer.upload(frm, data)))

    def import_paste(self, frm, text):
        return self.importer.view(self.importer.keep(*self.importer.paste(frm, text)))

    def import_status(self, token):
        return self.importer.view(token)

    def import_read(self, token, key):
        return self.importer.read(token, key)

    def import_run(self, token, keys, replace=False):
        results = self.importer.run(token, keys, replace)
        return {'results': results, 'list': self.importer.view(token), **self.view()}

    def import_install(self, token, key, sha):
        shown = self.importer.install(token, key, sha)
        return {**shown, 'list': self.importer.view(token), **self.installed()}

    def hint(self, suggestion_id, hint):
        return self.hints.add(suggestion_id, hint, self.mine())

    def hint_dismiss(self, hint_id):
        self.hints.dismiss(hint_id)
        return self.view()

    def hint_prompt(self, clawhub=False):
        """For the agent's review (installed skills only) and preparation turns (also ClawHub, which it can search):
        how to point the owner to a skill that would help, while there is room this week. '' when there isn't."""
        if not self.catalog or not self.hints.room():
            return ''
        listed = self.hints.candidates(self.mine())
        if not listed and not clawhub:
            return ''
        where = ('one that isn\'t ready here' + (' (below)' if listed else '') + ' or one on ClawHub (`openclaw skills search <what it does>`)'
                 if clawhub else 'one of the skills below that aren\'t ready here')
        return (f'Skills: if {where} would clearly help with this kind of work, add '
                'skill_hint:{"skill":"<its name' + (', or @owner/slug from ClawHub' if clawhub else '') + '>","why":"<one line>"} to '
                + ('prepare' if clawhub else 'suggest') + '. Judge it by what the card is about and only when it clearly helps: the owner sees it as a quiet '
                'line on the card and decides, so never install or turn on a skill yourself.' + ('\n' + listed if listed else ''))

    def _slug(self, db, name, keep_id=None):
        """A free slug for a name: not another skill's here, not a skill OpenClaw already has from elsewhere."""
        base, taken = slug_of(name), other_names(self.workspace / 'skills')
        taken |= {r['slug'] for r in db.execute("SELECT id,slug FROM skills WHERE status IN ('on','off')") if r['id'] != keep_id}
        slug, n = base, 2
        while slug in taken:
            slug, n = f'{base[:44]}-{n}', n + 1
        return slug

    def row(self, db, skill_id):
        row = db.execute('SELECT * FROM skills WHERE id=?', (str(skill_id or ''),)).fetchone()
        if not row:
            raise ValueError('That skill is gone')
        return row

    # -- the owner's own
    def save(self, skill_id, name, description, body, _db=None):
        if isinstance(body, str) and not (name or description) and FRONTMATTER.match(body):
            name, description, body = from_skill_md(body)
        name, description, body = validate(name, description, body)
        stamp, sha = now(), digest(name, description, body)
        with contextlib.nullcontext(_db) if _db is not None else self.connect() as db:
            if skill_id:
                row = self.row(db, skill_id)
                if row['status'] not in ('on', 'off'):
                    raise ValueError('Install or approve it first')
                if row['sha'] == sha:
                    return row['id'] if _db is not None else self.view()
                slug = row['slug'] if row['name'] == name else self._slug(db, name, keep_id=row['id'])
                saved_id = row['id']
                db.execute('UPDATE skills SET name=?,slug=?,description=?,body=?,sha=?,version=version+1,updated_at=? WHERE id=?',
                           (name, slug, description, body, sha, stamp, row['id']))
            else:
                if db.execute("SELECT COUNT(*) FROM skills WHERE status IN ('on','off')").fetchone()[0] >= MAX_SKILLS:
                    raise ValueError(f'At most {MAX_SKILLS} skills')
                saved_id = 'sk_' + secrets.token_hex(8)
                db.execute("INSERT INTO skills(id,slug,name,description,body,status,origin,sha,created_at,updated_at) VALUES(?,?,?,?,?,'on','mine',?,?,?)",
                           (saved_id, self._slug(db, name), name, description, body, sha, stamp, stamp))
        if _db is not None:
            return saved_id
        self.sync()
        return self.view()

    def toggle(self, skill_id, on):
        with self.connect() as db:
            row = self.row(db, skill_id)
            if row['status'] not in ('on', 'off'):
                raise ValueError('Install or approve it first')
            db.execute('UPDATE skills SET status=?,updated_at=? WHERE id=?', ('on' if on else 'off', now(), row['id']))
        self.sync()
        return self.view()

    def delete(self, skill_id):
        with self.connect() as db:
            row = self.row(db, skill_id)
            if row['status'] == 'incoming':
                raise ValueError('Decline it instead')
            db.execute('DELETE FROM skills WHERE id=?', (row['id'],))
        self.sync()
        return self.view()

    def get(self, skill_id):
        with self.connect() as db:
            row = self.row(db, skill_id)
            current = db.execute('SELECT body FROM skills WHERE id=?', (row['replaces'],)).fetchone() if row['replaces'] else None
        return {'id': row['id'], 'name': row['name'], 'description': row['description'], 'body': row['body'],
                **({'previous_body': current['body']} if current else {})}

    def example(self, key='customer-inquiries'):
        found = EXAMPLES.get(key)
        if not found:
            raise ValueError('No such example')
        return dict(found)

    # -- proposed by the agent
    def propose(self, name, description, body, why=''):
        """The agent noticed repeated work: a skill for the owner to approve. Never on before that."""
        name, description, body = validate(name, description, body)
        stamp, sha = now(), digest(name, description, body)
        with self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM skills WHERE status='proposed'").fetchone()[0] >= OPEN_PROPOSALS:
                raise ValueError(f'{OPEN_PROPOSALS} proposed skills already wait for the owner')
            if db.execute('SELECT 1 FROM skills WHERE sha=? OR (name=? AND status IN (\'on\',\'off\',\'proposed\'))', (sha, name)).fetchone():
                return {'ok': True, 'duplicate': True}
            skill_id = 'sk_' + secrets.token_hex(8)
            db.execute("INSERT INTO skills(id,slug,name,description,body,status,origin,sha,note,created_at,updated_at) VALUES(?,?,?,?,?,'proposed','proposed',?,?,?,?)",
                       (skill_id, slug_of(name), name, description, body, sha, one_line(why, NOTE_CHARS) or None, stamp, stamp))
        return {'ok': True, 'id': skill_id}

    # -- shared over the Network
    def targets(self):
        """Where a skill can go: each Fleet (everyone in it, or one teammate) and each friend."""
        fleet = self.fleet
        if not fleet:
            return []
        out = []
        try:
            for ref, found in fleet.fleets().items():
                if 'skill' not in (found.get('asks') or ()):  # the Network carries skills to this group only once it says so
                    continue
                people = [{'member_id': m['member_id'], 'name': fleet_module().label(m) or 'Someone'} for m in fleet.teammates(ref)]
                if people:
                    out.append({'fleet': ref, 'kind': found.get('kind'), 'name': found.get('name') or fleet_module().FLEET_NAMES.get(found.get('kind'), 'Fleet'),
                                'members': people})
        except (ValueError, KeyError, TypeError):
            return []
        return out

    def share(self, skill_id, fleet_ref, to=None, note=''):
        if not self.fleet:
            raise ValueError('Join a Fleet or add a friend first')
        target = next((t for t in self.targets() if t['fleet'] == fleet_ref), None)
        if not target:
            raise ValueError('Pick a Fleet or a friend')
        if to is not None:
            to = [m for m in to if any(p['member_id'] == m for p in target['members'])] if isinstance(to, list) else []
            if not to:
                raise ValueError('Pick who gets it')
        if target['kind'] == 'friends' and (not to or len(to) != 1):
            raise ValueError('Pick one friend')
        with self.connect() as db:
            row = self.row(db, skill_id)
            if row['status'] not in ('on', 'off'):
                raise ValueError('Install or approve it first')
        question = {'name': row['name'], 'slug': row['slug'], 'description': row['description'], 'body': row['body'],
                    'version': row['version'], 'sha': row['sha'], **({'note': one_line(note, NOTE_CHARS)} if one_line(note, NOTE_CHARS) else {})}
        sent = self.fleet.ask('skill', question, context=f"skill:{row['id']}:{row['version']}", fleet=fleet_ref, to=to)
        with self.connect() as db:
            shared = json.loads(self.row(db, skill_id)['shared'] or '[]')
            shared = [s for s in shared if s.get('fleet') != fleet_ref or s.get('to') != to][-19:]
            shared.append({'fleet': fleet_ref, 'to': to, 'version': row['version'], 'at': now()})
            db.execute('UPDATE skills SET shared=? WHERE id=?', (json.dumps(shared), row['id']))
        return {'sent': len(sent.get('ids', {})), **self.view()}

    def take_share(self, ask, teammate, fleet_ref, name):
        """A skill from a teammate or friend: kept for the owner to read, then install or decline. Returns the status
        the sender's Machine is told."""
        question = parse_share(ask.get('question'))
        if not teammate or not question:
            return 'declined'
        stamp = now()
        with self.connect() as db:
            previous = db.execute('SELECT status FROM skills WHERE ask_id=? AND fleet=? AND member_id=?',
                                  (str(ask.get('id') or ''), fleet_ref, teammate['member_id'])).fetchone()
            if previous:
                return 'waiting' if previous['status'] == 'incoming' else 'answered'
            if db.execute("SELECT COUNT(*) FROM skills WHERE status='incoming' AND fleet=? AND member_id=?",
                          (fleet_ref, teammate['member_id'])).fetchone()[0] >= OPEN_PER_SENDER:
                return 'declined'
            installed = db.execute("SELECT * FROM skills WHERE origin='shared' AND status IN ('on','off') AND fleet=? AND member_id=? AND their_slug=? "
                                   'ORDER BY updated_at DESC', (fleet_ref, teammate['member_id'], question['slug'])).fetchone()
            if installed and installed['sha'] == question['sha']:
                return 'answered'
            db.execute("DELETE FROM skills WHERE status='incoming' AND fleet=? AND member_id=? AND their_slug=?",
                       (fleet_ref, teammate['member_id'], question['slug']))
            db.execute("INSERT OR IGNORE INTO skills(id,slug,name,description,body,status,origin,version,sha,author,fleet,member_id,ask_id,their_slug,replaces,note,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,'incoming','shared',?,?,?,?,?,?,?,?,?,?,?)",
                       ('sk_' + secrets.token_hex(8), question['slug'], question['name'], question['description'], question['body'],
                        question['version'], question['sha'], one_line(name, 80) or 'A teammate', fleet_ref, teammate['member_id'], str(ask.get('id') or ''), question['slug'],
                        installed['id'] if installed else None, question.get('note'), stamp, stamp))
        return 'waiting'

    def decide(self, skill_id, choice):
        """The owner's Install / Decline on a shared skill, or Approve / Dismiss on a proposed one."""
        with self.connect() as db:
            row = self.row(db, skill_id)
            if row['status'] not in ('incoming', 'proposed'):
                raise ValueError('Already decided')
            if choice in ('install', 'approve'):
                if row['replaces'] and db.execute('SELECT 1 FROM skills WHERE id=?', (row['replaces'],)).fetchone():
                    db.execute('UPDATE skills SET name=?,description=?,body=?,sha=?,version=?,author=?,updated_at=? WHERE id=?',
                               (row['name'], row['description'], row['body'], row['sha'], row['version'], row['author'], now(), row['replaces']))
                    db.execute('DELETE FROM skills WHERE id=?', (row['id'],))
                else:
                    if db.execute("SELECT COUNT(*) FROM skills WHERE status IN ('on','off')").fetchone()[0] >= MAX_SKILLS:
                        raise ValueError(f'At most {MAX_SKILLS} skills')
                    db.execute("UPDATE skills SET status='on',slug=?,updated_at=? WHERE id=?", (self._slug(db, row['name']), now(), row['id']))
            elif choice in ('decline', 'dismiss'):
                db.execute('DELETE FROM skills WHERE id=?', (row['id'],))
            else:
                raise ValueError('Choose install or decline')
        if row['status'] == 'incoming' and row['ask_id'] and self.fleet:
            installed = choice in ('install', 'approve')
            try:
                self.fleet.relay.call('POST', '/answer', {'id': row['ask_id'], 'status': 'answered' if installed else 'declined',
                                                          **({'answer': {'stage': 'installed'}} if installed else {})})
            except (ValueError, OSError) as error:
                LOG.warning('skill answer error=%s', type(error).__name__)
        self.sync()
        return self.view()

    def shares_out(self, db, skill_id):
        rows = db.execute("SELECT name,status,answer,context FROM fleet_out WHERE kind='skill' AND context LIKE ? ORDER BY at DESC LIMIT 20",
                          (f'skill:{skill_id}:%',)).fetchall() if self.fleet else []
        label = {'asked': 'sent', 'waiting': 'reading it', 'answered': 'installed', 'declined': 'declined', 'expired': 'no answer'}
        seen, out = set(), []
        for r in rows:
            if r['name'] in seen:
                continue
            seen.add(r['name'])
            out.append({'name': r['name'], 'status': label.get(r['status'], r['status']), 'version': int(r['context'].rsplit(':', 1)[-1] or 0)})
        return out

    def view(self):
        with self.connect() as db:
            ensure(db)
            notes_kept = 'merged_into' in {c[1] for c in db.execute('PRAGMA table_info(facts)')}
            rows = db.execute("SELECT * FROM skills ORDER BY CASE status WHEN 'incoming' THEN 0 WHEN 'proposed' THEN 1 ELSE 2 END, name COLLATE NOCASE").fetchall()
            items = []
            for r in rows:
                item = {'id': r['id'], 'name': r['name'], 'description': r['description'], 'status': r['status'], 'origin': r['origin'],
                        'version': r['version'], 'author': r['author'] or '', 'note': r['note'] or '', 'update': bool(r['replaces']),
                        'chars': len(r['body']), 'updated_at': r['updated_at']}
                if r['imported']:
                    item['imported'] = json.loads(r['imported'])
                if r['status'] in ('on', 'off'):
                    if notes_kept:  # what the owner taught it, moved here from What I've learned
                        item['notes'] = [n[0] for n in db.execute("SELECT text FROM facts WHERE merged_into=? ORDER BY id", ('skill:' + r['id'],))]
                    item['shares'] = self.shares_out(db, r['id'])
                    item['changed'] = any(s['version'] < r['version'] for s in item['shares'])
                items.append(item)
        waiting = sum(1 for i in items if i['status'] in ('incoming', 'proposed'))
        return {'skills': items, 'waiting': waiting, 'targets': self.targets(), 'folder': f'skills/{FOLDER}', 'hints': self.hints.open(),
                'openclaw': bool(self.catalog), 'import_local': self.importer.local}


# -- Everything OpenClaw has (docs/skills.md): read from OpenClaw itself, never written here --------------------------
# The Machine's own skills are the ones above. OpenClaw also has bundled skills, ones in the workspace, learned ones and
# ones from ClawHub. Those are shown read-only: their files are never edited or removed from here. Turning one on or off
# is OpenClaw's own setting (skills.entries.<key>.enabled, which OpenClaw applies without a restart), and installing
# one from ClawHub is OpenClaw's own `openclaw skills install`, only on the owner's tap after reading it.
CATALOG_SECONDS, SEARCH_SECONDS = 1800, 600
HINTS_PER_WEEK = 3
REF = re.compile(r'@([A-Za-z0-9][A-Za-z0-9_.-]{0,63})/([a-z0-9][a-z0-9-]{0,79})')
VERSION = re.compile(r'[0-9A-Za-z][0-9A-Za-z.+-]{0,39}')
KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


def json_in(text, key=None):
    """The JSON object in a CLI's output, also when OpenClaw printed notices or log lines around it (a plugin being
    loaded, a config note, or its own logs as JSON lines): with `key`, the first whole object that has that key,
    otherwise the first whole object. None when there is none."""
    text = ANSI.sub('', text or '')
    try:
        found = json.loads(text)
        if isinstance(found, (dict, list)) and (key is None or isinstance(found, dict) and key in found):
            return found
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for start in re.finditer(r'^[ \t]*\{', text, re.M):
        try:
            found, _ = decoder.raw_decode(text, start.end() - 1)
        except ValueError:
            continue
        if isinstance(found, dict) and (key is None or key in found):
            return found
    return None


TEXT_FILES = ('.md', '.txt', '.json', '.yaml', '.yml', '.csv', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp')
OS_NAMES = {'darwin': 'macOS', 'linux': 'Linux', 'win32': 'Windows'}
SOURCES = {'openclaw-workspace': 'Workspace', 'openclaw-bundled': 'Bundled with OpenClaw', 'openclaw-workshop': 'Learned by OpenClaw',
           'openclaw-custodian': 'OpenClaw upkeep', 'openclaw-managed': 'Shared on this computer', 'openclaw-extra': 'From a plugin'}


def needs_text(missing, key=''):
    """What a skill still needs, in a few plain words; '' when nothing."""
    missing = missing if isinstance(missing, dict) else {}
    out = []
    bins = [str(b) for b in missing.get('bins') or []]
    if bins:
        out.append('the ' + ', '.join(bins[:3]) + (' commands' if len(bins) > 1 else ' command'))
    any_bins = [str(b) for b in missing.get('anyBins') or []]
    if any_bins:
        out.append('one of ' + ', '.join(any_bins[:3]))
    env = [str(e) for e in missing.get('env') or []]
    if env:
        out.append(', '.join(env[:3]))
    config = [str(c) for c in missing.get('config') or [] if str(c) != f'skills.entries.{key}.enabled']
    if config:
        out.append('the setting ' + ', '.join(config[:2]))
    return ('Needs ' + ' and '.join(out)) if out else ''


def os_only(missing):
    systems = [OS_NAMES.get(str(o), str(o)) for o in (missing or {}).get('os') or []] if isinstance(missing, dict) else []
    return (' or '.join(systems[:3]) + ' only') if systems else ''


def installed_item(raw, mine):
    """One skill OpenClaw lists, as the dashboard shows it."""
    name = one_line(raw.get('name'), 80)
    source = one_line(raw.get('source'), 60)
    missing = raw.get('missing') or {}
    only = os_only(missing)
    if name in mine:
        group = 'yours'
    elif source == 'openclaw-workspace':
        group = 'workspace'
    elif source == 'openclaw-bundled' or raw.get('bundled') is True:
        group = 'bundled'
    else:
        group = 'other'
    off = raw.get('disabled') is True
    blocked = raw.get('blockedByAllowlist') is True or raw.get('blockedByAgentFilter') is True
    ready = raw.get('eligible') is True and not off and not blocked
    state = 'ready' if ready else 'off' if off else 'blocked' if blocked else 'setup'
    need = only or needs_text(missing, name) or ('Not allowed for this agent' if blocked else '')
    return {'name': name, 'description': one_line(raw.get('description'), DESCRIPTION_CHARS), 'emoji': one_line(raw.get('emoji'), 4),
            'group': group, 'source': SOURCES.get(source, source.replace('openclaw-', '').replace('-', ' ').capitalize() or 'Other'),
            'state': state, 'on': not off, 'needs': need, 'toggle': group != 'yours' and not only}


class Catalog:
    """What OpenClaw itself sees (`openclaw skills list --json`), kept for half an hour and refreshed on demand, plus
    ClawHub search, reading a ClawHub skill before installing it, and OpenClaw's own install and on/off setting."""

    def __init__(self, config=None, run=subprocess.run, fetch=None, clock=time.time):
        config = config or {}
        self.binary = str(config.get('openclaw') or 'openclaw')
        self.registry = str(config.get('clawhub_registry') or 'https://clawhub.ai').rstrip('/')
        self.run, self.clock = run, clock
        self.fetch = fetch or self._fetch
        self.lock = threading.Lock()
        self.raw, self.at, self.error, self.searches = None, 0.0, '', {}

    def _cli(self, args, timeout=60):
        # No terminal and no colors: OpenClaw's own notices stay plain, and nothing waits on input
        env = {**os.environ, 'NO_COLOR': '1', 'FORCE_COLOR': '0'}
        try:
            done = self.run([self.binary, *args], capture_output=True, text=True, errors='replace', timeout=timeout,
                            stdin=subprocess.DEVNULL, env=env)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError('OpenClaw did not answer' + (' in time' if isinstance(error, subprocess.TimeoutExpired) else '')) from None
        return done

    @staticmethod
    def _reason(done):
        # In --json mode OpenClaw says why as {"ok": false, "error": {"message": ...}}
        for text in (done.stdout, done.stderr):
            said = (json_in(text, 'error') or {}).get('error')
            said = said.get('message') if isinstance(said, dict) else said
            if isinstance(said, str) and said.strip():
                return one_line(said, 300)
        lines = [ANSI.sub('', line).strip() for line in f'{done.stderr or ""}\n{done.stdout or ""}'.splitlines()]
        lines = [line for line in lines if line and not line.startswith(('OpenClaw ', '🦞'))]
        return one_line(lines[-1] if lines else 'OpenClaw said no', 300)

    def _json(self, args, timeout=60, key=None):
        done = self._cli([*args, '--json'], timeout)
        data = json_in(done.stdout, key)
        if data is None and not done.returncode:
            data = json_in(done.stderr, key)
        if data is None:
            if not done.returncode:  # what it looked like, for support: sizes and the first line, never the whole answer
                first = next((ANSI.sub('', line).strip() for line in (done.stdout or '').splitlines() if line.strip()), '')
                LOG.warning('openclaw %s unreadable stdout=%d lines=%d stderr=%d first=%r', ' '.join(args[:2]), len(done.stdout or ''),
                            len((done.stdout or '').splitlines()), len(done.stderr or ''), first[:120])
            raise ValueError(self._reason(done) if done.returncode else 'OpenClaw gave an answer The Machine could not read')
        return data

    def load(self):
        """Asks OpenClaw for every skill it has (about two seconds). Keeps the last good list when it fails."""
        try:
            try:
                data = self._json(['skills', 'list'], key='skills')
            except ValueError as error:
                # `skills list` gives the Gateway 1.5 seconds, which a busy small server can miss; the Gateway's own
                # skills.status, with time to answer, is the same report
                LOG.warning('skills list error=%s; asking the Gateway', error)
                try:
                    data = self._json(['gateway', 'call', 'skills.status', '--timeout', '30000', '--params', '{}'], 60, key='skills')
                except ValueError:
                    raise error from None
            found = data.get('skills') if isinstance(data, dict) else None
            if not isinstance(found, list):
                raise ValueError('OpenClaw gave an answer The Machine could not read')
            with self.lock:
                self.raw = [r for r in found if isinstance(r, dict) and isinstance(r.get('name'), str)][:500]
                self.at, self.error = self.clock(), ''
        except ValueError as error:
            LOG.warning('skills catalog error=%s', error)
            with self.lock:
                self.at, self.error = self.clock(), str(error)
        return self.raw

    def peek(self):
        """The list as last read, without waiting (the service's worker reads it every half hour)."""
        return self.raw

    def worker(self, stop):
        """Reads OpenClaw's list every half hour, a minute after the service starts; cheap (one CLI call)."""
        if stop.wait(60):
            return
        while True:
            if self.clock() - self.at >= CATALOG_SECONDS - 5:
                self.load()
            if stop.wait(60):
                return

    def installed(self, refresh=False):
        if refresh or self.raw is None or self.clock() - self.at > CATALOG_SECONDS:
            self.load()
        return self.raw or []

    def find(self, name):
        return next((r for r in self.installed() if r.get('name') == name), None)

    def view(self, mine, refresh=False):
        items = [installed_item(r, mine) for r in self.installed(refresh)]
        order = {'yours': 0, 'workspace': 1, 'bundled': 2, 'other': 3}
        items.sort(key=lambda i: (order[i['group']], i['name'].lower()))
        return {'installed': items, 'checked_at': dt.datetime.fromtimestamp(self.at, dt.timezone.utc).isoformat(timespec='seconds') if self.at else None,
                'error': self.error}

    def read(self, name):
        """One installed skill's SKILL.md, to read. Only read; nothing is written."""
        if not self.find(name):
            raise ValueError('OpenClaw doesn\'t have that skill')
        info = self._json(['skills', 'info', name])
        path = Path(str(info.get('filePath') or '')) if isinstance(info, dict) else Path()
        body = ''
        if path.name == 'SKILL.md' and path.is_file() and path.stat().st_size <= 256 * 1024:
            body = path.read_text(errors='replace')
        steps = [one_line(s.get('label') or s.get('kind'), 120) for s in (info.get('install') or []) if isinstance(s, dict)][:5]
        item = installed_item({**self.find(name)}, set())
        return {'name': name, 'description': item['description'], 'source': item['source'], 'state': item['state'], 'needs': item['needs'],
                'body': clean_body(body)[:BODY_CHARS * 2] or item['description'], 'install_steps': [s for s in steps if s],
                'homepage': info.get('homepage') if str(info.get('homepage') or '').startswith('https://') else ''}

    def toggle(self, name, on):
        """OpenClaw's own on/off for a skill (skills.entries.<key>.enabled): applied without a Gateway restart."""
        if not self.find(name):
            raise ValueError('OpenClaw doesn\'t have that skill')
        info = self._json(['skills', 'info', name])
        key = str(info.get('skillKey') or name) if isinstance(info, dict) else name
        if not KEY.fullmatch(key):
            raise ValueError('That skill can\'t be turned on or off from here')
        done = self._cli(['config', 'set', f'skills.entries.{key}.enabled', 'true' if on else 'false', '--strict-json'])
        if done.returncode:
            raise ValueError(self._reason(done))
        self.load()

    # -- ClawHub: third-party skills
    def search(self, query):
        query = one_line(query, 80)
        if len(query) < 2:
            raise ValueError('Type what the skill should help with')
        cached = self.searches.get(query.lower())
        if cached and self.clock() - cached[0] < SEARCH_SECONDS:
            return cached[1]
        data = self._json(['skills', 'search', *query.split()[:8], '--limit', '12'], timeout=60)
        have = {r.get('name') for r in self.installed()}
        out = []
        for r in (data.get('results') if isinstance(data, dict) else None) or []:
            found = REF.fullmatch(str(r.get('installRef') or '')) if isinstance(r, dict) else None
            if not found:  # only skills ClawHub itself hosts and scans (@owner/slug)
                continue
            native = (r.get('native') or {}).get('skill') or {}
            trust = r.get('trust') or {}
            out.append({'ref': found.group(0), 'owner': found.group(1), 'slug': found.group(2),
                        'name': one_line(r.get('displayName') or found.group(2), 80), 'summary': one_line(r.get('summary'), DESCRIPTION_CHARS),
                        'downloads': r['downloads'] if isinstance(r.get('downloads'), int) else 0,
                        'official': r.get('official') is True, 'suspicious': native.get('isSuspicious') is True,
                        'installable': trust.get('installability', 'installable') == 'installable', 'installed': found.group(2) in have,
                        'url': f'{self.registry}/{found.group(1)}/skills/{found.group(2)}'})
        self.searches = {k: v for k, v in self.searches.items() if self.clock() - v[0] < SEARCH_SECONDS}
        self.searches[query.lower()] = (self.clock(), out)
        return out

    def _fetch(self, url):
        request = urllib.request.Request(url, headers={'User-Agent': 'the-machine', 'Accept': 'application/json, text/plain'})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read(2 * 1024 * 1024 + 1)

    def _get(self, route, **params):
        url = self.registry + route + ('?' + urllib.parse.urlencode(params) if params else '')
        try:
            return self.fetch(url)
        except (OSError, urllib.error.URLError, ValueError):
            raise ValueError('ClawHub did not answer; try again in a minute') from None

    def hub(self, ref):
        """A ClawHub skill, read before installing: its SKILL.md as published, its files (so scripts show), whether it
        has install steps or asks for keys, and ClawHub's own scan. The sha is what an install must still match."""
        found = REF.fullmatch(str(ref or ''))
        if not found:
            raise ValueError('That isn\'t a ClawHub skill')
        owner, slug = found.group(1), found.group(2)
        try:
            detail = json.loads(self._get(f'/api/v1/skills/{slug}', ownerHandle=owner))
        except ValueError:
            raise ValueError('ClawHub doesn\'t have that skill') from None
        skill = detail.get('skill') if isinstance(detail, dict) else None
        if not isinstance(skill, dict) or skill.get('slug') != slug or ((detail.get('owner') or {}).get('handle') not in (None, owner)):
            raise ValueError('ClawHub doesn\'t have that skill')
        version = str((detail.get('latestVersion') or {}).get('version') or '')
        if not VERSION.fullmatch(version):
            raise ValueError('That skill has no release to install')
        try:
            release = json.loads(self._get(f'/api/v1/skills/{slug}/versions/{version}', ownerHandle=owner)).get('version') or {}
        except (ValueError, AttributeError):
            raise ValueError('ClawHub release files are unavailable; try again in a minute') from None
        if not isinstance(release, dict) or not isinstance(release.get('files'), list):
            raise ValueError('ClawHub release files are unavailable; try again in a minute')
        raw = self._get(f'/api/v1/skills/{slug}/file', path='SKILL.md', version=version, ownerHandle=owner)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('That skill is too large to read here')
        listed = next((f.get('sha256') for f in release.get('files') or [] if isinstance(f, dict) and f.get('path') == 'SKILL.md'), None)
        if listed and listed != hashlib.sha256(raw).hexdigest():
            raise ValueError('ClawHub sent a SKILL.md that doesn\'t match its release; not shown')
        text = raw.decode('utf-8', errors='replace')
        moderation = detail.get('moderation')
        files = [{'path': one_line(f.get('path'), 200), 'size': int(f.get('size') or 0)} for f in release.get('files') or [] if isinstance(f, dict)][:200]
        code = [f['path'] for f in files if not f['path'].lower().endswith(TEXT_FILES) or f['path'].split('/')[0] in ('scripts', 'bin')]
        top = FRONTMATTER.match(text)
        head = top.group(1) if top else ''
        security = release.get('security') if isinstance(release.get('security'), dict) else {}
        llm = ((security.get('scanners') or {}).get('llm') or {}) if isinstance(security.get('scanners'), dict) else {}
        return {'ref': found.group(0), 'owner': owner, 'slug': slug, 'name': one_line(skill.get('displayName') or slug, 80),
                'summary': one_line(skill.get('summary'), DESCRIPTION_CHARS), 'version': version, 'body': text,
                'sha': hashlib.sha256(raw).hexdigest(), 'files': files, 'code': code[:20],
                'install_steps': bool(re.search(r'["\']?install["\']?\s*:', head)),
                'asks_keys': bool(re.search(r'["\']?(?:env|primaryEnv|apiKey)["\']?\s*:', head)),
                'scan': one_line(security.get('status'), 40), 'scan_warnings': security.get('hasWarnings') is True,
                'scan_summary': one_line(llm.get('summary'), 400),
                'flagged': isinstance(moderation, dict) and (moderation.get('isSuspicious') is True or moderation.get('isMalwareBlocked') is True
                                                              or moderation.get('verdict') not in (None, 'clean')),
                'url': f'{self.registry}/{owner}/skills/{slug}'}

    def install(self, ref, version, sha):
        """OpenClaw's own install of exactly the release the owner read. Never forced, never past a policy warning."""
        shown = self.hub(ref)
        if shown['version'] != version or shown['sha'] != sha:
            raise ValueError('It changed since you read it; read it again first')
        if shown['slug'] in {r.get('name') for r in self.installed()}:
            raise ValueError('A skill with that name is already here')
        done = self._cli(['skills', 'install', shown['ref'], '--version', version], timeout=240)
        if done.returncode:
            raise ValueError(self._reason(done))
        self.load()
        try:  # what OpenClaw installed is what the owner read
            info = self._json(['skills', 'info', shown['slug']])
            got = ((info.get('clawhub') or {}).get('skillFile') or {}).get('sha256') if isinstance(info, dict) else None
        except ValueError:
            got = None
        if got and got != sha:
            LOG.warning('skills install sha differs ref=%s', shown['ref'])
            shown['differs'] = True
        return shown


class Hints:
    """"There's a skill for this": the agent names a skill that would clearly help with a card, judged from what the
    card is about. At most HINTS_PER_WEEK a week, never one the owner dismissed, never for a skill that is ready
    already (the agent uses those itself). A ClawHub one is only ever shown; installing it is the owner's tap."""

    def __init__(self, connect, catalog):
        self.connect, self.catalog = connect, catalog

    def room(self, db=None):
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat(timespec='seconds')
        if db is None:
            with self.connect() as db:
                return self.room(db)
        return db.execute('SELECT COUNT(*) FROM skill_hints WHERE created_at>?', (since,)).fetchone()[0] < HINTS_PER_WEEK

    def candidates(self, mine, limit=25):
        """Installed skills that aren't ready here, for the agent to weigh; '' when none or no room this week."""
        raw = self.catalog.peek() if self.catalog else None
        if not raw or not self.room():
            return ''
        with self.connect() as db:
            dismissed = {r[0] for r in db.execute("SELECT skill FROM skill_hints WHERE status='dismissed'")}
        lines = []
        for r in raw:
            item = installed_item(r, mine)
            if item['group'] == 'yours' or item['state'] == 'ready' or not item['toggle'] or item['name'] in dismissed:
                continue
            lines.append(f"- {item['name']}: {item['description'][:110]}" + (f" ({item['needs']})" if item['needs'] else ' (off)'))
        return '\n'.join(lines[:limit])

    def add(self, suggestion_id, hint, mine):
        """The agent's skill_hint on a card. Returns why not instead of failing the card."""
        if not isinstance(hint, dict):
            return {'ok': False, 'reason': 'skill_hint is {skill, why}'}
        skill, why = one_line(hint.get('skill'), 120).lstrip('$/'), one_line(hint.get('why'), 200)
        if len(why) < 5:
            return {'ok': False, 'reason': 'Say in one line why it helps (why)'}
        found = REF.fullmatch(skill)
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM suggestions WHERE id=?', (str(suggestion_id),)).fetchone():
                return {'ok': False, 'reason': 'Unknown suggestion'}
            if db.execute("SELECT 1 FROM skill_hints WHERE skill=? AND (status='dismissed' OR status='open')", (skill,)).fetchone():
                return {'ok': False, 'reason': 'Already suggested or dismissed by the owner'}
            if not self.room(db):
                return {'ok': False, 'reason': f'At most {HINTS_PER_WEEK} skill hints a week; this one is not shown'}
        if not self.catalog:
            return {'ok': False, 'reason': 'Skills are not available here'}
        try:
            if found:
                if found.group(2) in {r.get('name') for r in self.catalog.installed()}:
                    return {'ok': False, 'reason': 'It is installed already'}
                shown = self.catalog.hub(skill)
                kind, name, summary = 'clawhub', shown['name'], shown['summary']
            else:
                raw = self.catalog.find(skill)
                if not raw:
                    return {'ok': False, 'reason': 'No installed skill has that name; a ClawHub one is @owner/slug'}
                item = installed_item(raw, mine)
                if item['group'] == 'yours' or item['state'] == 'ready':
                    return {'ok': False, 'reason': 'It is ready already: use it yourself'}
                if not item['toggle']:
                    return {'ok': False, 'reason': 'It can\'t work on this computer'}
                kind, name, summary = 'installed', item['name'], item['description']
        except ValueError as error:
            return {'ok': False, 'reason': str(error)}
        stamp = now()
        with self.connect() as db:
            db.execute("INSERT INTO skill_hints(id,suggestion_id,skill,kind,name,summary,why,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'open',?,?)",
                       ('sh_' + secrets.token_hex(8), str(suggestion_id), skill, kind, name, summary, why, stamp, stamp))
        return {'ok': True, 'shown': 'on the card, as a quiet line the owner can dismiss'}

    def close(self, skill, status='done'):
        with self.connect() as db:
            db.execute("UPDATE skill_hints SET status=?,updated_at=? WHERE skill=? AND status='open'", (status, now(), skill))

    def dismiss(self, hint_id):
        with self.connect() as db:
            row = db.execute("SELECT skill FROM skill_hints WHERE id=?", (str(hint_id or ''),)).fetchone()
        if not row:
            raise ValueError('That hint is gone')
        self.close(row['skill'], 'dismissed')

    def open(self):
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat(timespec='seconds')
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='suggestions'").fetchone():
                return []
            return [{k: r[k] for k in ('id', 'suggestion_id', 'skill', 'kind', 'name', 'summary', 'why')}
                    for r in db.execute("SELECT h.* FROM skill_hints h JOIN suggestions s ON s.id=h.suggestion_id WHERE h.status='open' AND h.created_at>? "
                                        "AND s.status NOT IN ('completed','rejected') ORDER BY h.created_at DESC LIMIT 10", (since,))]


# -- Importing from Claude and ChatGPT (docs/skills.md#import-from-claude-or-chatgpt) -----------------------------------
# Both use the same SKILL.md form. What comes in is only what a skill here may be: a name, when to use it and the
# instructions, with reference .md files the instructions point to written into them. Scripts and other files never
# become The Machine's; a skill that needs them can be installed with them as OpenClaw's own, after reading it, like
# ClawHub. Files are read here, never run.
IMPORT_FROM = {'claude': 'Claude', 'chatgpt': 'ChatGPT'}
UPLOAD_BYTES = 6 * 1024 * 1024        # one upload from the dashboard
FILE_BYTES, TREE_BYTES, TREE_FILES = 1024 * 1024, 8 * 1024 * 1024, 300
SCAN_SECONDS, SCANS, CANDIDATES = 3600, 4, 200
SCRIPT_EXT = ('.py', '.sh', '.bash', '.zsh', '.fish', '.js', '.mjs', '.cjs', '.ts', '.rb', '.pl', '.php', '.ps1', '.bat', '.cmd',
              '.exe', '.bin', '.jar', '.go', '.rs', '.lua', '.swift', '.so', '.dylib', '.dll', '.wasm')
LICENCE_FILES = ('LICENSE', 'LICENCE', 'COPYING')
CLOSED = re.compile(r'proprietary|all rights reserved|may not (?:be )?(?:copied|shared|redistributed)|not (?:be )?redistribut', re.I)
TOOLS = {'Bash': 'exec', 'Read': 'read', 'Write': 'write', 'Edit': 'edit', 'MultiEdit': 'edit', 'WebFetch': 'web_fetch', 'WebSearch': 'web_search'}
FLAGS = ((re.compile(r'`(?:TodoWrite|Task|Agent|AskUserQuestion|NotebookEdit|Skill|Glob|Grep|ExitPlanMode)`'),
          'Names Claude Code tools OpenClaw has no tool of that name for'),
         (re.compile(r'\bmcp__\w+'), 'Calls connector (MCP) tools by name; they may not exist here'),
         (re.compile(r'/mnt/(?:user-data|skills|data)\b'), 'Uses folders of the claude.ai or ChatGPT sandbox (/mnt/…), which aren\'t here'),
         (re.compile(r'\$\{?CLAUDE_[A-Z_]+|~/\.claude\b|\$\{?CODEX_HOME|~/\.codex\b'), 'Refers to Claude Code or Codex folders'),
         (re.compile(r'\bartifacts?\b', re.I), 'Talks about artifacts, which The Machine doesn\'t show'))


def rewrite(text):
    """Claude Code's tool names where OpenClaw's are plainly the same tool (`Bash` is `exec`), and what else may not
    work here, flagged for the owner, never changed."""
    changed = set()

    def tick(found):
        changed.add(found.group(1))
        return '`' + TOOLS[found.group(1)] + '`'

    def named(found):
        changed.add(found.group(2))
        return found.group(1) + TOOLS[found.group(2)] + ' tool'
    names = '|'.join(TOOLS)
    text = re.sub(r'`(' + names + r')`', tick, text)
    text = re.sub(r'(\b[Tt]he )(' + names + r') tool\b', named, text)
    flags = ['Renamed tools to OpenClaw\'s: ' + ', '.join(f'{k} → {TOOLS[k]}' for k in sorted(changed))] if changed else []
    return text, flags + [note for pattern, note in FLAGS if pattern.search(text)]


def tree_digest(files):
    return hashlib.sha256(json.dumps(sorted([path, hashlib.sha256(data).hexdigest() if isinstance(data, bytes) else str(data)]
                                            for path, data in files.items())).encode()).hexdigest()


def keep_text(path):
    """Files kept in memory after reading: the ones that can become part of the instructions or tell the license."""
    name = path.rsplit('/', 1)[-1]
    return name.lower().endswith(('.md', '.txt')) or name.upper().startswith(LICENCE_FILES)


def read_tree(root, keep=keep_text):
    """A skill folder on this computer: {path: bytes}, with only a hash kept for scripts and other files, and None for
    one too big to read. Hidden files and anything linked from outside the folder are left out."""
    root = Path(root).resolve()
    files, total = {}, 0
    for top, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__'))
        for name in sorted(names):
            if name.startswith('.') or len(files) >= TREE_FILES:
                continue
            path = Path(top) / name
            try:
                real = path.resolve()
                if not real.is_relative_to(root) or not real.is_file():
                    continue
                size = real.stat().st_size
                rel = path.relative_to(root).as_posix()
                if size > FILE_BYTES or total + size > TREE_BYTES:
                    files[rel] = None
                    continue
                data = real.read_bytes()
            except OSError:
                continue
            total += size
            files[rel] = data if keep(rel) else 'sha256:' + hashlib.sha256(data).hexdigest()
    return files


def clean_path(name):
    parts = [p for p in str(name or '').replace('\\', '/').split('/') if p]
    if not parts or str(name).startswith('/') or any(p in ('..', '__MACOSX') or p.startswith('.') or ':' in p for p in parts):
        return None
    return '/'.join(parts)


def unzip(raw):
    """A .skill or .zip export: {path: bytes}. Read in memory, nothing unpacked to disk; size limits per file and in all."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        entries = archive.infolist()
    except (zipfile.BadZipFile, ValueError):
        raise ValueError('That isn\'t a .skill or .zip file') from None
    files, total = {}, 0
    for info in entries[:TREE_FILES * 3]:
        path = clean_path(info.filename)
        if info.is_dir() or not path or len(files) >= TREE_FILES:
            continue
        if info.file_size > FILE_BYTES or total + info.file_size > TREE_BYTES:
            files[path] = None
            continue
        try:
            with archive.open(info) as stream:
                data = stream.read(FILE_BYTES + 1)
        except (zipfile.BadZipFile, ValueError, OSError, NotImplementedError, RuntimeError):
            continue
        if len(data) > FILE_BYTES:
            files[path] = None
            continue
        total += len(data)
        files[path] = data
    return files


def split_skills(files):
    """The skills in a set of files: each folder with a SKILL.md, with the files under it (the nearest one's)."""
    roots = sorted({p.rsplit('/', 1)[0] if '/' in p else '' for p in files if p.rsplit('/', 1)[-1].lower() == 'skill.md'},
                   key=lambda r: -len(r))
    out = {r: {} for r in roots}
    for path, data in files.items():
        root = next((r for r in roots if not r or path.startswith(r + '/')), None)
        if root is not None:
            out[root][path[len(root) + 1:] if root else path] = data
    return out


def mentioned(path, text):
    name = path.rsplit('/', 1)[-1]
    return path in text or (len(name) >= 5 and re.search(r'(?<![\w.-])' + re.escape(name) + r'(?![\w-])', text) is not None)


def short(description):
    """"When to use it" in at most DESCRIPTION_CHARS: whole sentences when they fit, else cut at a word."""
    description = one_line(description, 4000)
    if len(description) <= DESCRIPTION_CHARS:
        return description, False
    cut = description[:DESCRIPTION_CHARS]
    end = max(cut.rfind('. '), cut.rfind('; '))
    return (cut[:end + 1] if end > 80 else cut[:DESCRIPTION_CHARS - 1].rsplit(' ', 1)[0] + '…'), True


def convert(files, frm, folder=''):
    """A skill's files as a skill here: (name, description, body) and what the owner should know about it."""
    md_path = next((p for p in files if p.lower() == 'skill.md'), None)
    text = files.get(md_path) if md_path else None
    if not isinstance(text, bytes):
        raise ValueError('No readable SKILL.md')
    text = text.decode('utf-8', errors='replace')
    fields = header(text)
    name = one_line(fields.get('name') or folder or 'Imported skill', NAME_CHARS)
    if len(name) < 3:
        name = (name + ' skill').strip()
    description, shortened = short(fields.get('description') or '')
    if len(description) < 10:
        description = one_line(f'When a task calls for {name}', DESCRIPTION_CHARS)
    body, flags = rewrite(clean_body(text))
    notes, left_out = [], []
    dropped = sorted(k for k in fields if k not in ('name', 'description', 'license', 'licence'))
    if dropped:
        notes.append('Header settings left out: ' + ', '.join(dropped[:5]))
    if shortened:
        notes.append('"When to use it" was shortened to fit')
    scripts = sorted(p for p in files if p.split('/')[0] in ('scripts', 'bin') or p.lower().endswith(SCRIPT_EXT))
    licence_files = [p for p in files if '/' not in p and p.upper().startswith(LICENCE_FILES)]
    references = [p for p in files if p.lower().endswith('.md') and p != md_path and p not in scripts and mentioned(p, body)]
    others = sorted(p for p in files if p not in (md_path, *scripts, *licence_files, *references))
    depends = any(mentioned(p, body) for p in scripts) or bool(scripts and re.search(r'\bscripts/', body))
    room = BODY_CHARS - 700
    if len(body) > room:
        cut = body[:room]
        end = max(cut.rfind('\n#'), cut.rfind('\n\n'))
        left_out.append(f'the end of its SKILL.md ({len(body) - (end if end > room // 2 else room):,} characters)')
        body = cut[:end if end > room // 2 else room].rstrip()
    for path in references:
        data = files.get(path)
        extra = rewrite(clean_body(data.decode('utf-8', errors='replace')))[0] if isinstance(data, bytes) else ''
        section = f'\n\n---\n\n## Reference: {path}\n\n{extra}'
        if extra and len(body) + len(section) <= room:
            body += section
        else:
            left_out.append(f'{path} (too long to include)' if extra else f'{path} (couldn\'t be read)')
    left_out += [p for p in others][:8] + ([f'and {len(others) - 8} more files'] if len(others) > 8 else [])
    if scripts:
        left_out.insert(0, f'{len(scripts)} script{"s" if len(scripts) > 1 else ""} ({", ".join(scripts[:3])}{", …" if len(scripts) > 3 else ""})')
        if depends:
            body += ('\n\n## Not included\nThis skill came with scripts that were not brought in (' + ', '.join(scripts[:5])
                     + (', …' if len(scripts) > 5 else '') + '). Steps that run them won\'t work here; say so instead of improvising.')
    licence_text = next((files[p][:4000].decode('utf-8', errors='replace') for p in licence_files if isinstance(files.get(p), bytes)), '')
    licence = one_line(fields.get('license') or fields.get('licence'), 80) or one_line(next((l for l in licence_text.splitlines() if l.strip()), ''), 80)
    closed = bool(CLOSED.search(fields.get('license') or fields.get('licence') or '') or CLOSED.search(licence_text))
    return {'name': name, 'description': description, 'body': body, 'flags': flags, 'notes': notes, 'left_out': left_out,
            'scripts': scripts, 'references': references, 'depends': depends, 'licence': licence, 'closed': closed,
            'files': len(files), 'original': text}


class Importer:
    """Skills found elsewhere, listed for the owner to tick (Settings > Skills > Import). A scan is kept in memory for
    an hour under a token, so what is imported is what was listed."""

    def __init__(self, keeper, home=None, local=True):
        self.keeper, self.home, self.local = keeper, Path(home or Path.home()), local
        self.scans, self.lock = {}, threading.Lock()

    # -- where to look
    def label(self, path):
        try:
            return '~/' + Path(path).relative_to(self.home).as_posix()
        except ValueError:
            return str(path)

    def folders(self):
        """(from, folder, kind, label, url) for each skill folder on this computer, Claude Code's and Codex's."""
        out = []

        def skills_in(base):
            try:
                return sorted(d for d in Path(base).iterdir() if not d.name.startswith('.') and (d / 'SKILL.md').is_file())
            except OSError:
                return []
        claude = self.home / '.claude' / 'skills'
        for d in skills_in(claude):
            out.append(('claude', d, 'local', 'Claude Code', ''))
        try:
            buckets = sorted(b for b in (claude / 'synced').iterdir() if b.is_dir() and not b.name.startswith('.'))
        except OSError:
            buckets = []
        for bucket in buckets:
            try:
                manifest = {s.get('skillId') or s.get('name'): s for s in json.loads((bucket / 'manifest.json').read_text()).get('skills') or []
                            if isinstance(s, dict)}
            except (OSError, ValueError, AttributeError):
                manifest = {}
            for d in skills_in(bucket):
                kind = 'builtin' if str((manifest.get(d.name) or {}).get('source') or '').startswith('anthropic') else 'synced'
                out.append(('claude', d, kind, 'claude.ai', 'https://claude.ai/customize/skills'))
        # claude.ai plugins synced by Claude Code: the owner's own uploads, else a vendor's (vendor caches elsewhere are left out)
        try:
            buckets = sorted(b for b in (self.home / '.claude' / 'plugins' / 'synced').iterdir() if b.is_dir() and not b.name.startswith('.'))
        except OSError:
            buckets = []
        for bucket in buckets:
            try:
                plugins = sorted(p for p in bucket.iterdir() if p.is_dir() and not p.name.startswith('.'))
            except OSError:
                continue
            for plugin in plugins:
                try:
                    market = str(json.loads((bucket / f'{plugin.name}.meta.json').read_text()).get('marketplace_name') or '')
                except (OSError, ValueError, AttributeError):
                    market = ''
                for d in skills_in(plugin / 'skills'):
                    out.append(('claude', d, 'synced' if market == 'My Uploads' else 'plugin', f'claude.ai plugin {plugin.name}', 'https://claude.ai/customize/skills'))
        codex = Path(os.environ.get('CODEX_HOME') or self.home / '.codex') / 'skills'
        for d in skills_in(codex):
            out.append(('chatgpt', d, 'local', 'Codex', ''))
        for d in skills_in(codex / '.system'):
            out.append(('chatgpt', d, 'builtin', 'Codex', ''))
        projects = []
        try:
            for top in sorted(self.home.iterdir())[:400]:
                if top.name.startswith('.') or top.name == 'node_modules' or not top.is_dir() or top.is_symlink():
                    continue
                projects.append(top / '.claude' / 'skills')
                try:
                    projects += [sub / '.claude' / 'skills' for sub in sorted(top.iterdir())[:200]
                                 if not sub.name.startswith('.') and sub.name != 'node_modules' and sub.is_dir() and not sub.is_symlink()]
                except OSError:
                    pass
        except OSError:
            pass
        for base in projects:
            for d in skills_in(base):
                out.append(('claude', d, 'project', 'Project', ''))
        return out[:CANDIDATES]

    # -- what is here already
    def taken(self):
        with self.keeper.connect() as db:
            rows = db.execute("SELECT name,sha,imported FROM skills WHERE status IN ('on','off','proposed')").fetchall()
        keys = {json.loads(r['imported']).get('key') for r in rows if r['imported']}
        names = {r['name'].lower() for r in rows}
        catalog = self.keeper.catalog.peek() if self.keeper.catalog else None
        others = {str(r.get('name') or '').lower() for r in catalog or [] if isinstance(r, dict)} - set(self.keeper.mine())
        others |= other_names(self.keeper.workspace / 'skills')
        return keys, names, {r['sha'] for r in rows}, others

    def item(self, key, frm, files, *, source, kind='upload', url='', folder='', link='', taken=None):
        """One skill as listed: what would come in and what the owner should know first."""
        try:
            done = convert(files, frm, folder)
        except ValueError as error:
            return None, str(error)
        keys, names, shas, others = taken or self.taken()
        name, description, body = done['name'], done['description'], done['body']
        try:
            validate(name, description, body)
        except ValueError as error:
            return None, str(error)
        group = ('builtin' if done['closed'] or kind == 'builtin' else 'vendor' if kind == 'plugin' or link and not Path(link).is_relative_to(self.home)
                 else 'project' if kind == 'project' else 'yours')
        duplicate = ('Already in your skills' if key in keys or name.lower() in names or digest(name, description, body) in shas
                     else 'OpenClaw already has a skill with this name' if slug_of(name) in others or name.lower() in others else '')
        public = {'key': key, 'name': name, 'description': description, 'from': frm, 'from_label': IMPORT_FROM.get(frm, frm),
                  'source': source, 'url': url if url.startswith('https://') else '', 'group': group, 'files': done['files'],
                  'scripts': done['scripts'][:5], 'script_count': len(done['scripts']), 'depends': done['depends'],
                  'references': done['references'][:5], 'left_out': done['left_out'], 'flags': done['flags'] + done['notes'],
                  'licence': done['licence'], 'blocked': 'Can\'t import: its license doesn\'t allow it' if done['closed'] else '',
                  'duplicate': duplicate, 'existing': self.existing(key, name, description, body) if duplicate == 'Already in your skills' else None,
                  'done': '', 'skill_id': '', 'recommended': False, 'why': '', **({'link': link} if link else {})}
        return {'public': public, 'files': files, 'converted': (name, description, body), 'original': done['original'],
                'sha': tree_digest(files)}, ''

    def existing(self, key, name, description, body):
        """The skill here that an import would duplicate: its id, and whether importing again may replace it (only the
        owner's own; a teammate's or a proposed one is never overwritten from an upload)."""
        with self.keeper.connect() as db:
            rows = db.execute("SELECT id,name,sha,status,origin,imported FROM skills WHERE status IN ('on','off','proposed')").fetchall()
        sha = digest(name, description, body)
        for test in (lambda r: r['imported'] and json.loads(r['imported']).get('key') == key, lambda r: r['sha'] == sha,
                     lambda r: r['name'].lower() == name.lower()):
            row = next((r for r in rows if test(r)), None)
            if row:
                return {'id': row['id'], 'replaceable': row['origin'] == 'mine' and row['status'] in ('on', 'off'), 'same': row['sha'] == sha}
        return None

    def keep(self, items, errors, finding=None):
        token = secrets.token_hex(12)
        with self.lock:
            stamp = time.time()
            self.scans = {k: v for k, v in sorted(self.scans.items(), key=lambda kv: kv[1]['at'])[-(SCANS - 1):] if stamp - v['at'] < SCAN_SECONDS}
            self.scans[token] = {'at': stamp, 'items': {i['public']['key']: i for i in items}, 'errors': errors[:10], 'finding': finding,
                                 'browser': {}, 'looked': {}}
        return token

    @staticmethod
    def brief(items):
        """The listed skills as the Find my skills turn reads them."""
        return [{'key': i['public']['key'], 'name': i['public']['name'], 'description': i['public']['description'][:200],
                 'group': i['public']['group'], 'blocked': bool(i['public']['blocked']), 'here': bool(i['public']['duplicate'])}
                for i in items]

    def scan(self, token):
        with self.lock:
            found = self.scans.get(str(token or ''))
        if not found or time.time() - found['at'] > SCAN_SECONDS:
            raise ValueError('That list is out of date; look again')
        return found

    def view(self, token):
        found = self.scan(token)
        order = {'yours': 0, 'project': 1, 'vendor': 2, 'builtin': 3}
        items = sorted((i['public'] for i in found['items'].values()), key=lambda p: (order.get(p['group'], 3), not p['recommended'], p['name'].lower()))
        return {'token': token, 'items': items, 'errors': found['errors'], 'finding': found['finding'], 'browser': found['browser'],
                'local': self.local, 'sources': self.sources(found, items)}

    def sources(self, found, items):
        """Where Find my skills looks, each with its own state: 'starting' (the turn hasn't opened the browser yet),
        'looking', 'done', 'error', 'signed_out' or 'unavailable', how many skills came from there, and the page it read
        (`where`) with what went wrong there (`detail`), so a miss says exactly where it looked. [] for an upload."""
        looked = found.get('looked') or {}
        if not found['finding'] and not looked:
            return []
        out = [{'key': 'local', 'state': 'done', 'found': sum(1 for p in items if ':account:' not in p['key'])}] if self.local else []
        for site in IMPORT_FROM:
            if site in looked:
                browser = found['browser'].get(site)
                out.append({'key': site, 'state': browser if browser in ('signed_out', 'unavailable') else looked[site],
                            'found': sum(1 for p in items if p['from'] == site and ':account:' in p['key']),
                            **({k: v for k, v in (found.get('where') or {}).get(site, {}).items() if k in ('where', 'detail')})})
        return out

    # -- listing
    def find(self):
        """Every skill on this computer that could come in (on a self-hosted Machine), listed at once."""
        items, errors, taken = [], [], self.taken()
        seen = set()
        for frm, folder, kind, where, url in (self.folders() if self.local else []):
            try:
                real = folder.resolve()
            except OSError:
                continue
            if real in seen:  # the same folder linked from Claude Code and Codex: listed once
                continue
            seen.add(real)
            link = str(real) if folder.is_symlink() else ''
            files = read_tree(folder)
            source = (f'claude.ai, built in ({folder.name})' if kind == 'builtin' and where == 'claude.ai' else
                      f'{where}, uploaded by you' if where.startswith('claude.ai plugin') and kind == 'synced' else
                      where if where.startswith('claude.ai plugin') else self.label(folder))
            item, error = self.item(f'{frm}:{self.label(folder)}', frm, files, source=source, kind=kind, url=url, folder=folder.name,
                                    link=link, taken=taken)
            if item:
                item['path'] = str(folder)
                items.append(item)
            elif error:
                errors.append(f'{self.label(folder)}: {error}')
        return items, errors

    def upload(self, frm, data):
        """An export the owner uploaded: a .skill or .zip (base64), or a folder's files ([{path, data}] in base64)."""
        if frm not in IMPORT_FROM:
            raise ValueError('Choose Claude or ChatGPT')
        if not isinstance(data, dict):
            raise ValueError('Nothing was uploaded')
        label = one_line(data.get('name'), 120) or 'Upload'
        try:
            if isinstance(data.get('zip'), str):
                raw = base64.b64decode(data['zip'], validate=True)
                if len(raw) > UPLOAD_BYTES:
                    raise ValueError('That file is too big (6 MB at most)')
                files = unzip(raw)
            elif isinstance(data.get('files'), list):
                files, total = {}, 0
                for f in data['files'][:TREE_FILES]:
                    path = clean_path(f.get('path')) if isinstance(f, dict) else None
                    if not path or not isinstance(f.get('data'), str):
                        continue
                    raw = base64.b64decode(f['data'], validate=True)
                    total += len(raw)
                    if total > UPLOAD_BYTES:
                        raise ValueError('That folder is too big (6 MB at most)')
                    files[path] = raw
            else:
                raise ValueError('Nothing was uploaded')
        except (ValueError, TypeError) as error:
            raise ValueError(str(error) if 'big' in str(error) or 'skill' in str(error) else 'That upload couldn\'t be read') from None
        found = split_skills(files)
        if not found:
            raise ValueError('No SKILL.md in it')
        items, errors, taken = [], [], self.taken()
        for root, tree in list(found.items())[:50]:
            folder = root.rsplit('/', 1)[-1] if root else label.rsplit('.', 1)[0]
            item, error = self.item(f'{frm}:upload:{label}/{root}', frm, tree, source=f'{label}' + (f' › {root}' if root else ''), folder=folder, taken=taken)
            (items.append(item) if item else errors.append(f'{root or label}: {error}'))
        return items, errors

    def paste(self, frm, text):
        if frm not in IMPORT_FROM:
            raise ValueError('Choose Claude or ChatGPT')
        if not isinstance(text, str) or not FRONTMATTER.match(text.strip() + '\n'):
            raise ValueError('Paste a whole SKILL.md, starting with its --- header')
        text = text.strip() + '\n'
        item, error = self.item(f'{frm}:paste:{hashlib.sha256(text.encode()).hexdigest()[:16]}', frm, {'SKILL.md': text.encode()}, source='Pasted SKILL.md')
        if not item:
            raise ValueError(error)
        return [item], []

    # -- reading, importing, installing with scripts
    def read(self, token, key):
        found = self.scan(token)['items'].get(str(key))
        if not found:
            raise ValueError('That skill isn\'t in this list')
        name, description, body = found['converted']
        public = found['public']
        installable = bool(public['script_count'] and not public['blocked'] and self.keeper.catalog and (found.get('path') or self.complete(found)))
        return {**public, 'body': body, 'original': found['original'][:64000], 'sha': found['sha'], 'installable': installable}

    @staticmethod
    def complete(found):
        return all(isinstance(v, bytes) for v in found['files'].values())

    def run(self, token, keys, replace=False):
        """Imports the ticked skills as the owner's own (origin mine, with where they came from). Skips one that is
        here already or whose license doesn't allow it, and says why. With replace, one that is here already as the
        owner's own is updated in place instead (same id, next version)."""
        found = self.scan(token)
        if not isinstance(keys, list) or not keys:
            raise ValueError('Tick the skills to import')
        results, stamp = [], now()
        for key in [str(k) for k in keys][:CANDIDATES]:
            item = found['items'].get(key)
            if not item:
                results.append({'key': key, 'status': 'skipped', 'reason': 'Not in this list'})
                continue
            public = item['public']
            name, description, body = item['converted']
            keys_here, names, shas, _ = self.taken()
            reason = (public['blocked'] or ('Already in your skills' if key in keys_here or name.lower() in names or digest(name, description, body) in shas else ''))
            existing = self.existing(key, name, description, body) if reason == 'Already in your skills' else None
            if replace and existing and existing['replaceable']:
                imported = {'key': key, 'from': public['from'], 'source': public['source'], **({'url': public['url']} if public['url'] else {}),
                            'left_out': public['left_out'][:10], 'flags': public['flags'][:8]}
                sha = digest(name, description, body)
                with self.keeper.connect() as db:
                    row = self.keeper.row(db, existing['id'])
                    slug = row['slug'] if row['name'] == name else self.keeper._slug(db, name, keep_id=row['id'])
                    db.execute('UPDATE skills SET name=?,slug=?,description=?,body=?,sha=?,imported=?,version=version+?,updated_at=? WHERE id=?',
                               (name, slug, description, body, sha, json.dumps(imported), 0 if row['sha'] == sha else 1, stamp, row['id']))
                public.update(done='Replaced', skill_id=row['id'], existing=None)
                results.append({'key': key, 'name': name, 'status': 'replaced', 'id': row['id'], 'left_out': public['left_out'], 'flags': public['flags']})
                LOG.info('skill import replaced an existing one from=%s', public['from'])
                continue
            if reason:
                if existing:
                    public.update(duplicate=reason, existing=existing)
                results.append({'key': key, 'name': name, 'status': 'skipped', 'reason': reason})
                continue
            imported = {'key': key, 'from': public['from'], 'source': public['source'], **({'url': public['url']} if public['url'] else {}),
                        'left_out': public['left_out'][:10], 'flags': public['flags'][:8]}
            with self.keeper.connect() as db:
                if db.execute("SELECT COUNT(*) FROM skills WHERE status IN ('on','off')").fetchone()[0] >= MAX_SKILLS:
                    results.append({'key': key, 'name': name, 'status': 'skipped', 'reason': f'At most {MAX_SKILLS} skills'})
                    continue
                skill_id = 'sk_' + secrets.token_hex(8)
                db.execute("INSERT INTO skills(id,slug,name,description,body,status,origin,sha,imported,created_at,updated_at) "
                           "VALUES(?,?,?,?,?,'on','mine',?,?,?,?)",
                           (skill_id, self.keeper._slug(db, name), name, description, body,
                            digest(name, description, body), json.dumps(imported), stamp, stamp))
            # Shown as done from now on, not as a duplicate of itself: the list re-renders right after.
            public.update(duplicate='Already in your skills', done='Imported', skill_id=skill_id, existing=None)
            results.append({'key': key, 'name': name, 'status': 'imported', 'id': skill_id, 'left_out': public['left_out'], 'flags': public['flags']})
            LOG.info('skill imported from=%s scripts=%d', public['from'], public['script_count'])
        self.keeper.sync()
        return results

    def install(self, token, key, sha):
        """A skill that needs its scripts, installed with them as OpenClaw's own (`openclaw skills install <folder>`),
        exactly as the owner read it: never forced, never past a policy warning, never one whose license says no."""
        found = self.scan(token)['items'].get(str(key))
        if not found:
            raise ValueError('That skill isn\'t in this list')
        public, catalog = found['public'], self.keeper.catalog
        if public['blocked']:
            raise ValueError(public['blocked'])
        if not catalog:
            raise ValueError('OpenClaw isn\'t set up here')
        if found['sha'] != str(sha):
            raise ValueError('Read it first')
        slug = slug_of(public['name'])
        if slug == FOLDER or slug in {str(r.get('name') or '') for r in catalog.installed()} or slug in other_names(self.keeper.workspace / 'skills'):
            raise ValueError('A skill with that name is already here')
        temporary = None
        try:
            if found.get('path'):
                folder = Path(found['path'])
                if tree_digest(read_tree(folder)) != found['sha']:
                    raise ValueError('It changed since you read it; look again')
            elif self.complete(found):
                temporary = tempfile.mkdtemp(prefix='skill-import-')
                folder = Path(temporary) / slug
                for path, data in found['files'].items():
                    target = folder / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            else:
                raise ValueError('Upload the whole skill to install it with its files')
            done = catalog._cli(['skills', 'install', str(folder), '--as', slug], timeout=240)
            if done.returncode:
                raise ValueError(catalog._reason(done))
        finally:
            if temporary:
                shutil.rmtree(temporary, ignore_errors=True)
        catalog.load()
        public.update(duplicate='Installed in OpenClaw', done='Installed in OpenClaw')
        LOG.info('skill installed in openclaw from=%s scripts=%d', public['from'], public['script_count'])
        return {'installed_skill': slug}

    # -- what the agent found and judged (the "Find my skills" turn)
    def report(self, token, found, recommended, browser):
        """Account skills the agent read on claude.ai or chatgpt.com, and which skills it recommends, with why."""
        scan = self.scan(token)
        added, taken = 0, self.taken()
        for f in (found if isinstance(found, list) else [])[:60]:
            if not isinstance(f, dict) or f.get('from') not in IMPORT_FROM:
                continue
            body, name = f.get('body'), one_line(f.get('name'), NAME_CHARS)
            if not name or any(i['public']['from'] == f['from'] and i['public']['name'].lower() == name.lower() for i in scan['items'].values()):
                continue  # synced to this computer already, or listed twice
            if isinstance(f.get('zip'), str):  # the skill's own download (a .skill file), read like an upload
                try:
                    raw = base64.b64decode(f['zip'], validate=True)
                    trees = list(split_skills(unzip(raw)).values()) if len(raw) <= UPLOAD_BYTES else []
                except ValueError:
                    trees = []
                files = trees[0] if trees else None
            elif isinstance(body, str) and len(body) <= 200000:
                files = None
            else:
                continue
            if files is None:
                if not isinstance(body, str):
                    continue
                text = body if FRONTMATTER.match(body) else '---\nname: ' + json.dumps(name) + '\ndescription: ' + json.dumps(one_line(f.get('description'), 4000)) + '\n---\n' + body
                files = {'SKILL.md': text.encode()}
            site = 'claude.ai' if f['from'] == 'claude' else 'chatgpt.com'
            url = str(f.get('url') or '')
            item, _ = self.item(f"{f['from']}:account:{slug_of(name)}", f['from'], files, source=f'{site}, your account',
                                kind='builtin' if f.get('builtin') is True else 'account', url=url if url.startswith(f'https://{site}/') else '',
                                taken=taken)
            if item:
                if f.get('recommended') is True and not item['public']['blocked']:
                    item['public']['recommended'], item['public']['why'] = True, one_line(f.get('why'), 160)
                with self.lock:
                    scan['items'].setdefault(item['public']['key'], item)
                added += 1
        for r in (recommended if isinstance(recommended, list) else [])[:12]:
            item = scan['items'].get(str(r.get('key') if isinstance(r, dict) else ''))
            if item and not item['public']['blocked']:
                item['public']['recommended'], item['public']['why'] = True, one_line(r.get('why'), 160)
        if isinstance(browser, dict):
            scan['browser'].update({k: v for k, v in browser.items() if k in IMPORT_FROM and v in ('ok', 'signed_out', 'unavailable')})
        with self.lock:  # one report covers every account the turn read: those are done now, before the turn itself ends
            scan['looked'] = {k: 'done' if v in ('starting', 'looking') else v for k, v in scan.get('looked', {}).items()}
        return {'added': added}

    def accounts(self, token, states, where=None):
        """The service's own look, before the turn: is the owner signed in to claude.ai and chatgpt.com in this
        Machine's browser ('ok', 'signed_out' or 'unavailable')? So the list can offer a sign-in right away. `where`:
        per site, the page it read and what went wrong there, both shown to the owner."""
        with self.lock:
            scan = self.scans.get(token)
            if scan:
                for site, seen in (where or {}).items():
                    if site in IMPORT_FROM and isinstance(seen, dict):
                        scan.setdefault('where', {})[site] = {k: one_line(seen.get(k), 160) for k in ('where', 'detail') if seen.get(k)}
                scan['browser'].update({k: v for k, v in (states or {}).items() if k in IMPORT_FROM and v in ('ok', 'signed_out', 'unavailable')})
                looked = scan.setdefault('looked', {})
                for site in looked:  # the turn has the browser now
                    if looked[site] == 'starting':
                        looked[site] = 'looking'

    def finished(self, token, status):
        with self.lock:
            scan = self.scans.get(token)
            if scan and scan['finding']:
                scan['finding'] = {**scan['finding'], 'status': status}
                looked = scan.setdefault('looked', {})  # a turn that ended without reporting a site: done, or couldn't check
                for site in looked:
                    if looked[site] in ('starting', 'looking'):
                        looked[site] = 'done' if status == 'ok' else 'error'


def fleet_module():
    import fleet
    return fleet
