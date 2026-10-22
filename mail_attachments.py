"""Email attachments at rest (SECURITY.md, "Message contents at rest").

A file a Do-it run takes from an email is that email's content, so with message protection on (message_seal.py) it is
kept sealed with the same message key, as one sealed file (message_seal.FILE_MAGIC), in its job's attachments folder
(data/assistant-attachments/<job id>/ in the OpenClaw workspace). Files a run makes itself there (a filled-in form, a
CSV, a preview) stay as they are.

- `save`: an attachment the app fetches for a run (`assistant attachment --save`, Gmail and Outlook) is written
  sealed, and the run gets a readable copy in a private folder (`open_root`: the user's runtime folder, in memory,
  when there is one, else open-attachments/ next to the database; mode 700). Copies go once the run has ended.
- `open_copy`: a readable copy of a sealed attachment for a later run (`assistant attachment --open <path>`).
- `read`: a file's bytes, opened when sealed: attaching one to a draft, sending it, serving it on the dashboard.
- `sweep`: once a run has ended, attachments it saved with gog itself (`gog gmail attachment`, found in its own tool
  calls by gog's `path` line) are sealed in place, together with the draft store's copy of the same bytes, and its
  readable copies are removed. The first sweep at a start covers every earlier run, so attachments saved before this
  existed are sealed in place too.
- `open_all`: protection turned off: every sealed attachment is opened again in place.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import message_seal

OPEN_NAME = 'the-machine-open-attachments'
FETCHES = ('gmail attachment', 'mail_attachment', 'assistant attachment')  # a tool call that takes a file from an email
# gog prints `path<TAB><file>` for a saved attachment; the app's own save answers {"saved": <file>}. Both may sit in
# JSON once or twice over (a clipped tool result), so the tab, newline and quotes can come escaped.
GOG_PATH = re.compile(r'(?:^|\\+n|\n|")path(?:\t|\\+t)(.+?)(?=\\+n|\n|\\*"|$)')
SAVED = re.compile(r'\\*"saved\\*"\s*:\s*\\*"(.+?)\\*"')
LONG_RUN = dt.timedelta(hours=2)  # a run still not finished after this has ended without saying so (runs stop at 35 min)


def root(config):
    """The attachments folder, data/assistant-attachments/ in the OpenClaw workspace."""
    if config.get('attachment_root'):
        return Path(config['attachment_root']).expanduser()
    workspace = config.get('openclaw_workspace') or os.environ.get('OPENCLAW_WORKSPACE') or Path.home() / '.openclaw/workspace'
    return Path(workspace).expanduser() / 'data/assistant-attachments'


def open_root(config, create=False):
    """Where runs get readable copies: memory (the user's runtime folder) when there is one, else next to the database."""
    if config.get('open_attachment_dir'):
        base = Path(config['open_attachment_dir']).expanduser()
    elif os.environ.get('XDG_RUNTIME_DIR') and Path(os.environ['XDG_RUNTIME_DIR']).is_dir():
        base = Path(os.environ['XDG_RUNTIME_DIR']) / OPEN_NAME
    elif config.get('database'):
        base = Path(config['database']).expanduser().parent / 'open-attachments'
    else:
        return None
    if create:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        if base.stat().st_uid != os.getuid():
            raise ValueError('The private attachments folder belongs to another user')
        os.chmod(base, 0o700)
    return base


def sealer(config):
    """The message key's Sealer when message protection is on (the database is sealed, its key is here and config.json
    doesn't turn it off); else None."""
    database = config.get('database')
    if not database or config.get('message_protection') is False or not Path(database).expanduser().is_file():
        return None
    try:
        with closing(sqlite3.connect(f'file:{Path(database).expanduser()}?mode=ro', uri=True, timeout=10)) as db:
            if not message_seal.is_protected(db):
                return None
    except sqlite3.Error:
        return None
    key = message_seal.load_key(database)
    return message_seal.Sealer(key) if key else None


def opener(config):
    """The message key's Sealer when the key is here at all: sealed files open also while protection is being turned off."""
    key = message_seal.load_key(config['database']) if config.get('database') else None
    return message_seal.Sealer(key) if key else None


def _write(path, data, scrub=False):
    """Atomic, mode 600. Scrub the old inode only after the durable replacement, never before it.

    A failed rename leaves the original intact. After a successful rename, an open handle lets us overwrite the
    unlinked old inode without touching the replacement. This is best effort, not secure erasure on every filesystem.
    """
    path = Path(path)
    old, temp = None, None
    try:
        if scrub:
            try:  # the old file itself, never what a symlink in its place points to (os.replace swaps only the link)
                old = os.fdopen(os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)), 'r+b')
            except FileNotFoundError:
                pass
            except OSError:
                if not path.is_symlink():
                    raise
        fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
        temp = Path(name)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
        if os.name != 'nt':
            folder = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(folder)
            finally:
                os.close(folder)
        if old:
            size = os.fstat(old.fileno()).st_size
            for start in range(0, size, 1 << 20):
                old.write(b'\0' * min(1 << 20, size - start))
            old.flush()
            os.fsync(old.fileno())
    finally:
        if old:
            old.close()
        if temp:
            temp.unlink(missing_ok=True)


def read(path, config):
    """A file's bytes, opened when it is a sealed attachment (the key is needed then)."""
    data = Path(path).read_bytes()
    if not message_seal.is_sealed_bytes(data):
        return data
    key = opener(config)
    if not key:
        raise ValueError(f'This attachment is encrypted and {message_seal.KEY_NAME} is missing')
    return key.open_bytes(data)


def from_email(path, config):
    """A file that came from an email: sealed at rest, or a readable copy in the private folder."""
    base = open_root(config)
    real = Path(path).resolve()
    return message_seal.is_sealed_file(real) or bool(base and real.is_relative_to(base.resolve()))


def plain_name(name):
    """An attachment's file name as given, when it is only a name: no folder, no '..', nothing absolute."""
    name = str(name or '')
    if (not name or name in ('.', '..') or name != Path(name).name or '\\' in name or '\0' in name
            or Path(name).is_absolute() or re.match(r'^[A-Za-z]:', name)):
        raise ValueError('Give the attachment a plain file name')
    return name


def save(config, folder, name, data):
    """Keep one attachment the app fetched from an email for a run: sealed in the job's folder when protection is on,
    with a readable copy for the run in the private folder. Returns {'saved': what the run reads, 'sealed': at rest}."""
    folder = Path(folder)
    name = plain_name(name)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    seal = sealer(config)
    if not seal:
        _write(folder / name, data)
        return {'saved': str(folder / name)}
    _write(folder / name, seal.seal_bytes(data))
    copy = open_root(config, create=True) / folder.name
    copy.mkdir(mode=0o700, exist_ok=True)
    _write(copy / name, data)
    return {'saved': str(copy / name), 'sealed': str(folder / name)}


def open_copy(config, path, job):
    """A readable copy of a sealed attachment in the job's attachments folders, for a run (removed after the run)."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(job or '')):
        raise ValueError('Give the job_id of this preparation')
    real = Path(str(path)).expanduser().resolve()
    if not real.is_relative_to(root(config).resolve()) or not real.is_file():
        raise ValueError('Give a file in the attachments folder (data/assistant-attachments/<job id>/)')
    if not message_seal.is_sealed_file(real):
        return {'saved': str(real)}
    copy = open_root(config, create=True) / job
    copy.mkdir(mode=0o700, exist_ok=True)
    _write(copy / real.name, read(real, config))
    return {'saved': str(copy / real.name), 'sealed': str(real)}


def store_copy(config, path, data, from_mail):
    """The draft store's copy (next_actions.store_attachments): sealed when the file came from an email."""
    seal = sealer(config) if from_mail else None
    if Path(path).exists() and (not seal or message_seal.is_sealed_file(path)):
        return
    _write(path, seal.seal_bytes(data) if seal else data, scrub=bool(seal))


def seal_in_place(config, path, seal, store=None):
    """Seal one plain attachment where it is (and the draft store's copy of the same bytes). True when it changed."""
    path = Path(path)
    data = path.read_bytes()
    if message_seal.is_sealed_bytes(data):
        return False
    if store:
        kept = Path(store) / hashlib.sha256(data).hexdigest()
        if kept.is_file() and not message_seal.is_sealed_file(kept):
            _write(kept, seal.seal_bytes(data), scrub=True)
    _write(path, seal.seal_bytes(data), scrub=True)
    return True


def _texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)


def fetched(params, result, base, workspace):
    """The files under `base` one tool call took from an email: gog's saved path, or the app's own save."""
    params, result = str(params or ''), str(result or '')
    if not any(word in params for word in FETCHES):
        return []
    found = []
    for text in [result, *_texts(_json(result))]:
        found += [m.strip() for m in GOG_PATH.findall(text)] + SAVED.findall(text)
    paths = []
    for name in dict.fromkeys(found):
        name = name.replace('\\\\', '\\')
        path = Path(name) if name.startswith('/') else Path(workspace) / name
        try:
            path = path.resolve()
        except (OSError, RuntimeError):
            continue
        if path.is_relative_to(base) and path.is_file():
            paths.append(path)
    return paths


def _json(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def sweep(connect, config, since=None, store=None, now=None):
    """Seal what ended runs took from emails and remove their readable copies. `since`: only runs that ended since then
    (an ISO time); None: every earlier run. Returns how many files were sealed."""
    now = now or dt.datetime.now(dt.timezone.utc)
    base, sealed = root(config).resolve(), 0
    workspace = base.parent.parent
    with connect() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'preparation_runs', 'preparation_calls'} <= tables:
            return 0
        recent = (now - LONG_RUN).isoformat(timespec='seconds')
        busy = {r[0] for r in db.execute('SELECT job_id FROM preparation_runs WHERE finished_at IS NULL AND started_at>? '
                                         'UNION SELECT session_job_id FROM preparation_runs WHERE finished_at IS NULL AND started_at>?',
                                         (recent, recent))}
        seal = sealer(config)
        if seal and base.is_dir():
            jobs = [r[0] for r in db.execute('SELECT DISTINCT job_id FROM preparation_runs WHERE finished_at>=?', (since,))] if since else None
            query = "SELECT job_id,params,result FROM preparation_calls WHERE result IS NOT NULL"
            rows = (db.execute(query + ' AND job_id IN (SELECT value FROM json_each(?))', (json.dumps(jobs),)).fetchall() if jobs
                    else db.execute(query).fetchall() if jobs is None else [])
            for job, params, result in rows:
                if job in busy:
                    continue
                for path in fetched(params, result, base, workspace):
                    if path.parent.name in busy:
                        continue
                    try:
                        sealed += seal_in_place(config, path, seal, store)
                    except OSError:
                        pass
    _tidy(config, busy, now)
    return sealed


def _tidy(config, busy, now, quiet=600):
    """Readable copies of runs that have ended go (a copy made in the last ten minutes stays: its run may just start)."""
    base = open_root(config)
    if not base or not base.is_dir():
        return
    for folder in base.iterdir():
        if folder.name in busy:
            continue
        try:
            if now.timestamp() - folder.stat().st_mtime < quiet:
                continue
            shutil.rmtree(folder) if folder.is_dir() else folder.unlink()
        except OSError:
            pass


def open_all(config, store=None):
    """Message protection turned off: every sealed attachment (and draft store file) opened in place."""
    key, opened = opener(config), 0
    if not key:
        return 0
    for folder in [root(config), Path(store) if store else None]:
        if not folder or not folder.is_dir():
            continue
        for path in folder.rglob('*'):
            if path.is_file() and message_seal.is_sealed_file(path):
                _write(path, key.open_bytes(path.read_bytes()))
                opened += 1
    return opened
