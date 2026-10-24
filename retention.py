"""Keeps the database and the local copies of it from growing without end.

Once a day, at a quiet hour (03:00-06:00 in the configured timezone, or the first chance after three days without a
run), the service:

- deletes sync history (`sync_activity`) older than 30 days, after adding it to per-source daily counts
  (`sync_daily`: syncs, failed, signals), which stay;
- deletes the tool calls of preparations (`preparation_calls`) older than 90 days, except those of a run that hasn't
  ended and those of a card that is still open (not done or dismissed);
- clears the evidence a profile or learnings run read (`profile_runs.evidence`) 90 days after it ended (the run, its
  times and its result stay);
- gives the freed space back: the first time with one VACUUM, which also switches the database to incremental
  vacuuming, then each night `PRAGMA incremental_vacuum` in small steps. The VACUUM only runs when no review,
  preparation or profile run is going on, and deletes go in small batches, so the service keeps working meanwhile;
- rotates local copies of the database (see rotate_backups).

`python3 retention.py rotate-backups --data-dir <folder> [--dry-run]` runs the rotation on its own, for scripts that
make a copy before they change something. `python3 retention.py run --config <config.json> [--dry-run]` runs it all."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

LOG = logging.getLogger('personal-assistant')

SYNC_DAYS = 30
PREPARATION_DAYS = 90
PROFILE_EVIDENCE_DAYS = 90
BACKUP_ALL_DAYS = 7          # every local copy from the last 7 days stays
BACKUP_DAYS = 30             # then the newest copy of each database per day, up to 30 days; older ones go
QUIET_HOURS = (3, 6)         # local time, from 03:00 until 06:00
OVERDUE_HOURS = 72           # a Machine that sleeps at night runs it at the first chance after this long
BATCH = 2000                 # rows per short write transaction
PAUSE = 0.05                 # between batches, so other writers get their turn
VACUUM_MAX_BYTES = 2 * 1024 ** 3   # a bigger database would hold the write lock too long for one VACUUM
INCREMENTAL_PAGES = 2000     # pages given back per step of the nightly incremental vacuum
CLOSED_CARDS = ('completed', 'rejected')

SQLITE_HEADER = b'SQLite format 3\x00'
COMPANIONS = ('-wal', '-shm', '-journal')
# Older copies next to the database (events.sqlite.bak-20261020, events.sqlite.before-merge, …); the copy message
# protection keeps while it switches (.before-protection) has its own life and is left alone.
COPY_NAME = re.compile(r'\.(?:bak|backup)(?:[-._]|$)|\.before-|\.pre-[^.]*\d{8}')
OWN_COPIES = re.compile(r'\.before-protection')
# Folders of copies next to the database: backups/, backups-manual/, repair-backups/, deployment-backup/, …
BACKUP_FOLDER = re.compile(r'backup', re.I)
# Folders with rules of their own: work removal deletes its copies after 7 days; agents-md-backups holds no database.
OTHER_RULES = ('work-removal-backups', 'agents-md-backups')


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(when):
    return when.astimezone(dt.timezone.utc).isoformat()


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS sync_daily(
        account_id TEXT NOT NULL,day TEXT NOT NULL,syncs INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,
        signals INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(account_id,day));
      CREATE TABLE IF NOT EXISTS retention_runs(at TEXT PRIMARY KEY,report TEXT NOT NULL);
    ''')


def _batches(connect, select, params, work, pause=PAUSE):
    """Runs `work(db, rowids)` on batches of the rows `select` finds, one short transaction each. Returns the count."""
    done = 0
    while True:
        with connect() as db:
            ids = [r[0] for r in db.execute(select + f' LIMIT {BATCH}', params)]
            if ids:
                work(db, json.dumps(ids))
        done += len(ids)
        if len(ids) < BATCH:
            return done
        time.sleep(pause)


def prune_sync(connect, now):
    """Sync history older than SYNC_DAYS goes; each day's counts per source stay in sync_daily first."""
    cutoff = _iso(now - dt.timedelta(days=SYNC_DAYS))

    def work(db, ids):
        db.execute('''INSERT INTO sync_daily(account_id,day,syncs,failed,signals)
                      SELECT account_id,substr(at,1,10),count(*),sum(status='failed'),coalesce(sum(signals),0)
                      FROM sync_activity WHERE rowid IN (SELECT value FROM json_each(?)) GROUP BY 1,2
                      ON CONFLICT(account_id,day) DO UPDATE SET syncs=syncs+excluded.syncs,
                      failed=failed+excluded.failed,signals=signals+excluded.signals''', (ids,))
        db.execute('DELETE FROM sync_activity WHERE rowid IN (SELECT value FROM json_each(?))', (ids,))
    return _batches(connect, 'SELECT rowid FROM sync_activity WHERE at<?', (cutoff,), work)


def prune_preparations(connect, now):
    """Tool calls of preparations older than PREPARATION_DAYS go, unless their run is still going or their card is
    still open."""
    cutoff = _iso(now - dt.timedelta(days=PREPARATION_DAYS))
    with connect() as db:
        if not {'preparation_calls', 'preparation_runs'} <= _tables(db):
            return 0
    closed = ','.join(f"'{s}'" for s in CLOSED_CARDS)
    select = f'''SELECT c.rowid FROM preparation_calls c WHERE c.started_at<?
        AND NOT EXISTS(SELECT 1 FROM preparation_runs r WHERE (r.job_id=c.job_id OR r.session_job_id=c.job_id)
                       AND (r.finished_at IS NULL OR r.finished_at>=?))
        AND NOT EXISTS(SELECT 1 FROM preparations p WHERE (p.job_id=c.job_id OR p.session_job_id=c.job_id)
                       AND p.status IN ('starting','running'))
        AND NOT EXISTS(SELECT 1 FROM preparation_runs r JOIN suggestions s ON s.id=r.suggestion_id
                       WHERE (r.job_id=c.job_id OR r.session_job_id=c.job_id) AND s.status NOT IN ({closed}))
        AND NOT EXISTS(SELECT 1 FROM preparations p JOIN suggestions s ON s.id=p.suggestion_id
                       WHERE (p.job_id=c.job_id OR p.session_job_id=c.job_id) AND s.status NOT IN ({closed}))'''
    return _batches(connect, select, (cutoff, cutoff),
                    lambda db, ids: db.execute('DELETE FROM preparation_calls WHERE rowid IN (SELECT value FROM json_each(?))', (ids,)))


def prune_profile_evidence(connect, now):
    """What a profile or learnings run read is cleared PROFILE_EVIDENCE_DAYS after it ended; the run stays."""
    cutoff = _iso(now - dt.timedelta(days=PROFILE_EVIDENCE_DAYS))
    with connect() as db:
        if 'evidence' not in {r[1] for r in db.execute('PRAGMA table_info(profile_runs)')}:
            return 0
    return _batches(connect, "SELECT rowid FROM profile_runs WHERE evidence IS NOT NULL AND finished_at IS NOT NULL AND finished_at<? "
                             "AND status NOT IN ('starting','running','recovering')", (cutoff,),
                    lambda db, ids: db.execute('UPDATE profile_runs SET evidence=NULL WHERE rowid IN (SELECT value FROM json_each(?))', (ids,)))


def give_back_space(connect, database, busy=None):
    """Free pages go back to the file system. The first time a VACUUM (which also turns on incremental vacuuming), only
    when nothing runs and the database isn't huge; after that small incremental steps. Returns what it did."""
    with connect() as db:
        mode = db.execute('PRAGMA auto_vacuum').fetchone()[0]
        free = db.execute('PRAGMA freelist_count').fetchone()[0]
        pages = db.execute('PRAGMA page_count').fetchone()[0]
        size = pages * db.execute('PRAGMA page_size').fetchone()[0]
        if mode != 2:
            if busy is None:
                import message_seal
                busy = message_seal.work_running(database)
            if busy != 0:
                return {'vacuum': 'skipped', 'reason': 'work is running'}
            if size > VACUUM_MAX_BYTES:
                return {'vacuum': 'skipped', 'reason': 'database too large for one VACUUM'}
            db.isolation_level = None
            db.execute('PRAGMA auto_vacuum=INCREMENTAL')
            db.execute('VACUUM')  # in WAL mode readers keep reading; writers wait (their busy timeout) for a few seconds
            with contextlib.suppress(sqlite3.Error):
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            return {'vacuum': 'full', 'free_pages': free}
        given = 0
        db.isolation_level = None
        while free > 0:
            step = min(free, INCREMENTAL_PAGES)
            db.execute(f'PRAGMA incremental_vacuum({step})').fetchall()
            given += step
            left = db.execute('PRAGMA freelist_count').fetchone()[0]
            if left >= free:
                break
            free = left
            time.sleep(PAUSE)
        with contextlib.suppress(sqlite3.Error):
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return {'vacuum': 'incremental', 'free_pages': given}


def _is_sqlite(path):
    try:
        with open(path, 'rb') as handle:
            return handle.read(16) == SQLITE_HEADER
    except OSError:
        return False


def _which_database(path):
    """Which database a copy is of, so each one keeps its own copy per day: 'events' for the main database (it has
    events and suggestions), else the first word of its name."""
    try:
        uri = 'file:' + str(path).replace('?', '%3f').replace('#', '%23') + '?mode=ro&immutable=1'
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
            if {'events', 'suggestions'} <= _tables(db):
                return 'events'
    except sqlite3.Error:
        pass
    match = re.match(r'[A-Za-z]+', path.name)
    return match[0].lower() if match else path.name


def backup_copies(data_dir, live=()):
    """Every local copy of a database the rotation looks after: SQLite files named as a copy next to the database,
    and SQLite files anywhere in a backup folder next to it. Live databases and their companions never count."""
    data_dir = Path(data_dir).expanduser()
    keep = {Path(p).expanduser().resolve() for p in live}
    found = []
    if not data_dir.is_dir():
        return found

    def take(path):
        if path.is_symlink() or not path.is_file() or path.name.endswith(COMPANIONS) or path.resolve() in keep:
            return
        if _is_sqlite(path):
            found.append(path)

    for item in sorted(data_dir.iterdir()):
        if item.name.startswith('.') or item.is_symlink():
            continue
        if item.is_file() and COPY_NAME.search(item.name) and not OWN_COPIES.search(item.name):
            take(item)
        elif item.is_dir() and BACKUP_FOLDER.search(item.name) and item.name not in OTHER_RULES:
            for folder, dirs, files in os.walk(item):
                dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in OTHER_RULES
                                 and not os.path.islink(os.path.join(folder, d)))
                for name in sorted(files):
                    take(Path(folder) / name)
    return found


def rotate_backups(data_dir, now=None, live=(), dry_run=False, local_tz=None):
    """Every copy from the last 7 days stays; from 7 to 30 days old, the newest copy of each database per day (in
    local time); anything older than 30 days goes. A copy's age is its file's modification time. Its -wal, -shm and
    -journal files go with it. Only SQLite files are removed: notes and settings copied beside them stay."""
    now = now or utc_now()
    zone = local_tz or dt.datetime.now().astimezone().tzinfo
    data_dir = Path(data_dir).expanduser()
    copies = []
    for path in backup_copies(data_dir, live):
        try:
            stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        except OSError:
            continue
        copies.append((stamp, path))
    copies.sort(reverse=True)  # newest first
    newest_of_day, gone, kept, freed = set(), [], 0, 0
    for stamp, path in copies:
        age = now - stamp
        if age <= dt.timedelta(days=BACKUP_ALL_DAYS):
            kept += 1
            continue
        if age <= dt.timedelta(days=BACKUP_DAYS):
            key = (_which_database(path), stamp.astimezone(zone).date())
            if key not in newest_of_day:
                newest_of_day.add(key)
                kept += 1
                continue
        for item in [path] + [path.with_name(path.name + end) for end in COMPANIONS]:
            with contextlib.suppress(OSError):
                if item.is_file() and not item.is_symlink():
                    freed += item.stat().st_size
                    if not dry_run:
                        item.unlink()
        gone.append(str(path.relative_to(data_dir)))
        if not dry_run:  # folders the rotation emptied go too, never the backup folder itself
            folder = path.parent
            while folder != data_dir and folder.parent != data_dir:
                try:
                    folder.rmdir()
                except OSError:
                    break
                folder = folder.parent
    return {'kept': kept, 'removed': len(gone), 'freed_bytes': freed, 'removed_files': gone, 'dry_run': dry_run}


def quiet(now, zone, last_at):
    """Whether tonight's run is due: once a day at the quiet hour, or at once after OVERDUE_HOURS without one."""
    last = dt.datetime.fromisoformat(last_at) if last_at else None
    if last and now - last < dt.timedelta(hours=20):
        return False
    if last and now - last >= dt.timedelta(hours=OVERDUE_HOURS):
        return True
    return QUIET_HOURS[0] <= now.astimezone(zone).hour < QUIET_HOURS[1]


WAITING = json.dumps({'waiting': True})


def last_run(connect):
    """(last real run, first check) as ISO times, None where there is none."""
    with connect() as db:
        if 'retention_runs' not in _tables(db):
            return None, None
        row = db.execute('SELECT max(CASE WHEN report!=? THEN at END),min(at) FROM retention_runs', (WAITING,)).fetchone()
        return row[0], row[1]


def due(connect, now, zone):
    """quiet() with the last run from the database. The first check ever only notes the time, so a Machine that is
    never on at night still gets its first run OVERDUE_HOURS later, and none starts in the middle of the day right
    after an update."""
    last, first = last_run(connect)
    if first is None:
        with connect() as db:
            initialize(db)
            db.execute('INSERT OR IGNORE INTO retention_runs VALUES(?,?)', (_iso(now), WAITING))
        first = _iso(now)
    if last is None:
        return (QUIET_HOURS[0] <= now.astimezone(zone).hour < QUIET_HOURS[1]
                or now - dt.datetime.fromisoformat(first) >= dt.timedelta(hours=OVERDUE_HOURS))
    return quiet(now, zone, last)


def run(connect, database, now=None, live=(), rotate=True, busy=None, local_tz=None):
    """Tonight's retention: old log rows go, the space goes back, local copies rotate. Returns the report."""
    now = now or utc_now()
    with connect() as db:
        initialize(db)
    report = {'sync_activity': prune_sync(connect, now),
              'preparation_calls': prune_preparations(connect, now),
              'profile_evidence': prune_profile_evidence(connect, now)}
    try:
        report['space'] = give_back_space(connect, database, busy)
    except sqlite3.Error as error:  # tried again tomorrow
        report['space'] = {'vacuum': 'failed', 'reason': type(error).__name__}
    if rotate:
        rotated = rotate_backups(Path(database).expanduser().parent, now, live=[database, *live], local_tz=local_tz)
        report['backups'] = {k: v for k, v in rotated.items() if k != 'removed_files'}
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO retention_runs VALUES(?,?)', (_iso(now), json.dumps(report)))
        db.execute('DELETE FROM retention_runs WHERE at<?', (_iso(now - dt.timedelta(days=BACKUP_DAYS)),))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    rotate = sub.add_parser('rotate-backups', help='rotate local copies of the database')
    rotate.add_argument('--data-dir', default='~/.local/share/personal-assistant')
    rotate.add_argument('--dry-run', action='store_true')
    rotate.add_argument('--list', action='store_true', help='also list what goes')
    every = sub.add_parser('run', help='prune old log rows, give space back and rotate copies, now')
    every.add_argument('--config', default='~/.local/share/personal-assistant/config.json')
    args = parser.parse_args(argv)
    if args.command == 'rotate-backups':
        data_dir = Path(args.data_dir).expanduser()
        live = []
        config = data_dir / 'config.json'
        with contextlib.suppress(OSError, ValueError):
            live.append(json.loads(config.read_text())['database'])
        live += [data_dir / 'events.sqlite', data_dir / 'assistant.sqlite']
        result = rotate_backups(data_dir, live=live, dry_run=args.dry_run)
        if not args.list:
            result.pop('removed_files')
        print(json.dumps(result, indent=1))
        return 0
    config = json.loads(Path(args.config).expanduser().read_text())
    database = str(Path(config['database']).expanduser())
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import assistant  # the service's own connection: sealed columns stay sealed
    store = assistant.Store(database)
    zone = ZoneInfo(config.get('timezone') or 'Europe/Amsterdam')
    print(json.dumps(run(store.connect, database, local_tz=zone), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
