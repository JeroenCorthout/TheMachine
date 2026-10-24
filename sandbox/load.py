"""Builds a sandbox install from the fixture: a config, credentials and a database in one directory, in one of
several states. Nothing here is real; the config points at no real service and turns every sender off.

    python3 sandbox/load.py --dir /tmp/machine-sandbox --state busy

States (combine with commas, e.g. fleet,friends):
    fresh        just installed: two accounts, the first signals, no cards and no review yet
    reviews-off  a normal install with reviews switched off
    busy         a full day: every source, open and prepared cards, promises, decisions, reviews
    fleet        busy, in a work Fleet with three teammates (one without a Machine), their questions and a seat request
    friends      busy, with two friends, a request in and one out
    family       busy, in a Family Fleet
    expired      busy, with the work Google sign-in expired (Gmail and Calendar)
    all          busy + fleet + friends + family
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATES = ('fresh', 'reviews-off', 'busy', 'fleet', 'friends', 'family', 'expired', 'all')
HOST = 'machine.sandbox.example'  # the dashboard's host name; run.py's proxy sends it
EXPIRED = 'google_signin_expired_run_gog_auth_add'


def parse_states(text):
    states = {s.strip() for s in str(text or 'busy').split(',') if s.strip()}
    unknown = states - set(STATES)
    if unknown:
        raise SystemExit(f"unknown state {', '.join(sorted(unknown))}; choose from {', '.join(STATES)}")
    if 'all' in states:
        states |= {'busy', 'fleet', 'friends', 'family'}
    return states


def groups(states):
    """The Network groups this state is in: work, family, friends."""
    return [g for g, s in (('work', 'fleet'), ('family', 'family'), ('friends', 'friends')) if s in states]


try:
    from zoneinfo import ZoneInfo
    BRUSSELS = ZoneInfo('Europe/Brussels')  # the sandbox owner's timezone (config 'timezone')
except Exception:  # no tz database (Windows without tzdata): winter time is close enough for made-up meetings
    BRUSSELS = dt.timezone(dt.timedelta(hours=1))


def stamp(minutes_ago, base):
    return (base - dt.timedelta(minutes=minutes_ago)).isoformat()


def write_secret(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(value)


def make_config(directory, fixture, states, port, network_port):
    accounts = fixture['accounts']
    if 'fresh' in states:
        accounts = [a for a in accounts if a['id'] in ('work-gmail', 'work-calendar')]
    credentials = {}
    for name in ('api-token', 'owner-token') + (('fleet',) if groups(states) else ()):
        write_secret(directory / name, secrets.token_hex(24))
        credentials[name] = str(directory / name)
    config = {
        'owner_name': fixture['owner']['name'], 'timezone': 'Europe/Brussels', 'telegram_owner': '',
        'tailscale_owner': fixture['owner']['email'], 'dashboard_host': HOST, 'dashboard_path': '/', 'dashboard_aliases': [],
        'port': port, 'database': str(directory / 'events.sqlite'), 'credentials': credentials,
        'app_dir': str(ROOT), 'openclaw': '/nonexistent/openclaw', 'node': '/nonexistent/node', 'gog': '/nonexistent/gog', 'wacli': '/nonexistent/wacli',
        'openclaw_state': str(directory / 'openclaw.sqlite'), 'agent_db': str(directory / 'agent.sqlite'),
        'delivery_enabled': False, 'digest_at': '08:30', 'bootstrap_days': 7, 'poll_seconds': 300, 'http_timeout': 15,
        'prompt_receipts': str(directory / 'prompt-receipts'), 'attachment_dir': str(directory / 'attachments'), 'attachment_sources': [],
        'booking_links': {'30-minute call': 'https://book.example/alex/30min'},
        'review_worker': {'enabled': 'reviews-off' not in states, 'min_signals': 40, 'max_wait_minutes': 60},
        'weekly_recap': {'enabled': False}, 'embeddings': {'enabled': False}, 'enrichment': {'enabled': False},
        'accounts': accounts, 'sandbox': True,
    }
    if groups(states):
        config['fleet'] = {'url': f'http://127.0.0.1:{network_port}/api/fleet'}
    path = directory / 'config.json'
    path.write_text(json.dumps(config, indent=2))
    return config, path


def working_hours(start, duration, slot):
    """The same day (a weekend moves to Friday or Monday), on a round half hour between 09:00 and 18:00 in Brussels,
    so a meeting reads like one whenever the sandbox starts. `slot` picks the half hour."""
    local = start.astimezone(BRUSSELS)
    day = local.date() + dt.timedelta(days={5: -1, 6: 1}.get(local.weekday(), 0))
    latest = (18 * 60 - duration) // 30  # the last half hour it still ends by 18:00
    minutes = 9 * 60 + 30 * (slot % (latest - 18 + 1))
    return dt.datetime.combine(day, dt.time(minutes // 60, minutes % 60), BRUSSELS).astimezone(dt.timezone.utc)


def calendar_meta(meta, base):
    start = working_hours(base - dt.timedelta(minutes=meta['start_minutes_ago']), meta['duration_minutes'], meta['start_minutes_ago'])
    end = start + dt.timedelta(minutes=meta['duration_minutes'])
    out = {k: v for k, v in meta.items() if k not in ('start_minutes_ago', 'duration_minutes', 'attendees', 'organizer')}
    return {**out, 'start': {'dateTime': start.isoformat()}, 'end': {'dateTime': end.isoformat()}, 'end_at': end.isoformat(), 'all_day': False,
            'timezone': 'Europe/Brussels', 'organizer': {'email': meta['organizer']},
            'attendees': [{'email': a, **({'self': True} if a == meta['organizer'] else {'responseStatus': 'accepted'})} for a in meta['attendees']]}


def load(directory, states, port=8870, network_port=8871, fixture_path=HERE / 'fixture.json'):
    """Writes the install into `directory` (emptied first) and returns (config, config_path)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('events.sqlite', 'events.sqlite-wal', 'events.sqlite-shm', 'sent.jsonl'):
        (directory / name).unlink(missing_ok=True)
    fixture = json.loads(Path(fixture_path).read_text())
    config, config_path = make_config(directory, fixture, states, port, network_port)
    os.environ['HOME'] = str(directory)  # before assistant is imported: nothing of the real install is read
    sys.path.insert(0, str(ROOT))
    import assistant
    import graph

    store = assistant.Store(config['database'])
    base = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    fresh = 'fresh' in states
    accounts = {a['id'] for a in config['accounts']}
    events = [e for e in fixture['events'] if e['account_id'] in accounts]
    if fresh:  # the first sync: a day of mail and the week's meetings
        events = [e for e in events if e['minutes_ago'] < 24 * 60]
    ids = {}
    with store.connect() as db:
        for e in events:
            meta = calendar_meta(e['metadata'], base) if e['kind'] == 'calendar.event' else e['metadata']
            body = e['body']
            at = meta['start']['dateTime'] if e['kind'] == 'calendar.event' else stamp(e['minutes_ago'], base)
            db.execute('INSERT INTO events(account_id,source_id,kind,occurred_at,title,body,metadata,deleted,content_hash,version,analyzed_version,ingested_at) '
                       'VALUES(?,?,?,?,?,?,?,0,?,1,?,?)',
                       (e['account_id'], 'sandbox:' + e['key'], e['kind'], at, e['title'], body, json.dumps(meta),
                        hashlib.sha256((e['title'] + body).encode()).hexdigest(), 0 if fresh else 1, stamp(max(e['minutes_ago'], 0) - 1, base)))
            ids[e['key']] = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        for account in config['accounts']:
            if 'expired' in states and account['id'] in ('work-gmail', 'work-calendar'):
                error = f"{account['service']}:{EXPIRED}"
                db.execute('INSERT INTO checkpoints(account_id,state,last_success,error,error_at,failures) VALUES(?,?,?,?,?,?)',
                           (account['id'], '{}', stamp(26 * 60, base), error, stamp(4, base), 31))
                for hours in range(0, 26, 2):
                    db.execute('INSERT INTO sync_activity VALUES(?,?,?,?,?,?,?)', (secrets.token_hex(16), account['id'], stamp(hours * 60 + 4, base), 'failed', None, error, None))
            else:
                db.execute('INSERT INTO checkpoints(account_id,state,last_success,last_sync_signals) VALUES(?,?,?,?)',
                           (account['id'], '{}', stamp(3, base), sum(1 for e in events if e['account_id'] == account['id'])))
                db.execute('INSERT INTO sync_activity VALUES(?,?,?,?,?,?,?)', (secrets.token_hex(16), account['id'], stamp(3, base), 'success', 2, None, None))
        if not fresh:
            load_cards(db, fixture, ids, base)
            load_reviews(db, ids, base, assistant)
            load_boards(db, fixture, ids, base)
    if not fresh:
        with store.connect() as db:
            load_job(db, fixture, base)
            db.commit()  # graph.build opens its own transaction
            graph.build(db, config, base.isoformat())
            load_roles(db, base)
            load_fields(db, base)
            load_skills(db, states, fixture, base)
            if 'fleet' in states:
                load_share(db)
                load_owners(db, fixture, base)
        load_logins(directory, fixture)
    return config, config_path


def load_owners(db, fixture, base):
    """Graph: a few projects owned by a teammate, picked by hand (crm.set_owner), so boards and Mine show the split."""
    import crm
    team = fixture['network']['work']['team']
    for name, m in (('Gullwing Clinics onboarding', 0), ('Halyard sync delay', 1), ('Fernwave', 2)):
        row = db.execute('SELECT id FROM projects WHERE name=?', (name,)).fetchone()
        if row:
            crm.set_owner(db, f'project:{row[0]}', team[m]['member_id'], team[m]['name'], stamp(2 * 24 * 60, base))
    db.commit()


def load_fields(db, base):
    """Graph: two organization fields and a few tags (crm_fields.py), as someone running it like a CRM would add."""
    import crm_fields
    when = stamp(3 * 24 * 60, base)
    plan = crm_fields.save_field(db, {'kind': 'org', 'name': 'Plan', 'type': 'choice', 'choices': 'Starter\nGrowth\nEnterprise'}, when)
    seats = crm_fields.save_field(db, {'kind': 'org', 'name': 'Seats', 'type': 'number'}, when)
    for domain, tier, count, tags in (('meridianrobotics.example', 'Enterprise', '120', ('Partner',)),
                                      ('harborlinehealth.example', 'Growth', '40', ('Pilot',)),
                                      ('northwindanalytics.example', None, None, ('Partner',))):
        node = f'org:{domain}'
        if not db.execute('SELECT 1 FROM graph_nodes WHERE id=?', (node,)).fetchone():
            continue
        if tier:
            crm_fields.set_value(db, node, plan['id'], tier, when)
            crm_fields.set_value(db, node, seats['id'], count, when)
        for label in tags:
            crm_fields.tag(db, node, label, now=when)
    db.commit()


def load_share(db):
    """The Shared Graph (shared_graph.py): the reviews' call on each Work email thread, as they would have made it.
    Client threads go to the team; one about something personal to the client stays private (its existence only)."""
    import shared_graph
    shared_graph.ensure(db)
    calls, sensitive = [], None
    for row in db.execute("SELECT id,title,metadata FROM events WHERE account_id='work-gmail' AND kind='gmail.message' ORDER BY occurred_at DESC"):
        meta = json.loads(row['metadata'] or '{}')
        people = shared_graph.addresses(meta)
        if not people:
            continue
        if all(a.endswith('@lumenlabs.example') for a in people):
            calls.append({'event_id': row['id'], 'share': 'internal'})
            continue
        who = str(meta.get('from') or '').split('<')[0].strip().split(' ')[0] or 'They'
        if sensitive is None and 'lumenlabs' not in str(meta.get('from') or ''):
            sensitive = row['id']
            calls.append({'event_id': row['id'], 'share': 'sensitive'})
            continue
        calls.append({'event_id': row['id'], 'share': 'client', 'line': f"{who} wrote about {' '.join(str(row['title'] or '').split())[:80]}"})
    shared_graph.judge(db, calls)


def load_job(db, fixture, base):
    """A card handed to Claude Code earlier, with its result: the card's job block (agent_jobs.py)."""
    import agent_jobs
    job = fixture.get('agent_job')
    if not job:
        return
    db.executescript(agent_jobs.SCHEMA)
    card_id = hashlib.sha256(job['card'].encode()).hexdigest()[:16]
    at = stamp(40, base)
    db.execute('INSERT INTO agent_jobs(id,suggestion_id,tool,place,status,brief,session_id,summary,files,turns,created_at,updated_at,started_at,finished_at) '
               'VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?)',
               ('sandbox-job-1', card_id, job['tool'], 'local', 'result', 'Answer the question on the card.', 'sandbox-session', job['summary'],
                json.dumps([{'path': f} for f in job['files']]), stamp(55, base), at, stamp(54, base), at))


def load_logins(directory, fixture):
    """Connections > Sign-ins: sites the browser signs back in to. Made-up sites; the password is a placeholder."""
    import browser_login
    import message_seal
    message_seal.load_key(directory / 'events.sqlite', create=True)  # the app makes it at start too; sealed in-process, no openssl
    logins = browser_login.LoginStore(directory)
    for entry in fixture.get('saved_logins', []):
        logins.save(entry['site'], entry['username'], 'sandbox-placeholder')


def load_roles(db, base):
    """Graph > Who's who: roles a review read for the four closest people, waiting for the owner to confirm."""
    import graph
    roles = [('head of operations', 'I run operations here, so send the rollout plan my way.'), ('finance lead', 'Looping in finance; I sign off on the invoice.'),
             ('product manager', 'As PM for the mobile app I own the beta list.'), ('customer success', 'I look after your account from our side.')]
    people = db.execute("SELECT id FROM graph_nodes WHERE kind='person' ORDER BY strength DESC LIMIT 4").fetchall()
    for (node_id,), (role, quote) in zip(people, roles):
        graph.set_role(db, node_id, role, 'review', base.isoformat(), quote=quote)


def load_skills(db, states, fixture, base):
    """Settings > Skills: the example as the owner's own, one the Machine proposes, and in a Fleet one a teammate shared."""
    import skills
    skills.ensure(db)
    example = skills.EXAMPLES['customer-inquiries']
    rows = [('sk_sandbox_mine', example, 'on', 'mine', None, None, None),
            ('sk_sandbox_proposed', {'name': 'Prepare the weekly pipeline update',
                                     'description': 'When the owner asks for the weekly pipeline numbers for the team call.',
                                     'body': '1. Count new, won and lost deals since last Monday.\n2. List the three largest open deals with their next step.\n3. Draft five lines for the owner to post.'},
             'proposed', 'proposed', None, None, 'You asked for this four Mondays in a row.')]
    if {'fleet', 'all'} & set(states):
        team = fixture['network']['work']
        rows.append(('sk_sandbox_shared', {'name': 'Qualify an inbound demo request before the first call',
                                           'description': 'When someone books a demo through the website form.',
                                           'body': '1. Look up the company: size, industry, current tools.\n2. Check earlier conversations with anyone there.\n3. Write three questions for the call.'},
                     'incoming', 'shared', team['team'][0]['name'], team['id'], 'Saved me an hour a day on demo prep.'))
    for skill_id, s, status, origin, author, fleet_id, note in rows:
        at = stamp(60 * 24, base)
        db.execute('INSERT OR REPLACE INTO skills(id,slug,name,description,body,status,origin,version,sha,author,fleet,member_id,note,created_at,updated_at) '
                   'VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)',
                   (skill_id, skills.slug_of(s['name']), s['name'], s['description'], s['body'], status, origin,
                    skills.digest(s['name'], s['description'], s['body']), author, fleet_id,
                    fixture['network']['work']['team'][0]['member_id'] if author else None, note, at, at))


def load_cards(db, fixture, ids, base):
    import graph
    for name in fixture['projects']:
        db.execute('INSERT OR IGNORE INTO projects(name,created_at) VALUES(?,?)', (name, stamp(30 * 24 * 60, base)))
    for card in fixture['cards']:
        event_ids = [ids[k] for k in card['events'] if k in ids]
        if not event_ids:
            continue
        card_id = hashlib.sha256(card['key'].encode()).hexdigest()[:16]
        created = stamp(card['minutes_ago'], base)
        snoozed = stamp(-card['snooze_hours'] * 60, base) if card.get('snooze_hours') else None
        db.execute('INSERT INTO suggestions(id,dedupe_key,title,body,event_ids,account_ids,status,created_at,snoozed_until,resume_status,next_action,'
                   'urgent,source_line,project) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (card_id, 'sandbox:' + card['key'], card['title'], card['body'], json.dumps(event_ids), json.dumps(card['accounts']), card['status'],
                    created, snoozed, 'pending' if snoozed else None, json.dumps(card['next_action']) if card.get('next_action') else None,
                    int(bool(card.get('urgent'))), f"Source: {card['source']} · {(base - dt.timedelta(minutes=card['minutes_ago'])).strftime('%a, %b %-d')}",
                    card.get('project')))
        db.execute('INSERT INTO deliveries(suggestion_id,status,updated_at) VALUES(?,?,?)', (card_id, 'sent', created))
        if card.get('project'):  # Graph > Projects: the card's thread carries its project, as a review labels it
            graph.label(db, event_ids, card['project'], 'review', created)
        if card.get('choice'):
            db.execute('INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES(?,?,?,?,?,?)',
                       ('d' + card_id, card_id, card['choice'], '', 'dashboard', stamp(max(card['minutes_ago'] - 30, 1), base)))
    for p in fixture['promises']:
        if p['event'] not in ids:
            continue
        due = (base + dt.timedelta(days=p['due_days'])).date().isoformat() if p.get('due_days') is not None else None
        account = db.execute('SELECT account_id FROM events WHERE id=?', (ids[p['event']],)).fetchone()[0]
        db.execute('INSERT OR IGNORE INTO promises(direction,who,what,due,check_at,event_id,account_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (p['direction'], p['who'], p['what'], due, (base + dt.timedelta(days=p['due_days'] or 1)).isoformat(), ids[p['event']], account,
                    p['status'], stamp(2 * 24 * 60, base), stamp(60, base)))


def load_boards(db, fixture, ids, base):
    """Graph > Projects > Board: each project labeled with its messages and moved through its stages by them, as a review
    would (graph.set_stage), one dragged by hand; their organizations' names as an enrichment service would give them."""
    import graph
    for board in fixture.get('boards') or ():
        if board.get('suggestion'):  # projects on no board yet, and a review's suggestion of a board for them
            sug = board['suggestion']
            for p in sug['projects']:
                graph.label(db, [ids[k] for k in p['events'] if k in ids], p['name'], 'review', base.isoformat())
            graph.suggest_board(db, {**sug, 'projects': [p['name'] for p in sug['projects']]}, 'review', base.isoformat())
            continue
        graph.save_board(db, {'name': board['name'], 'stages': board['stages'], 'about': board.get('about', '')}, base.isoformat())  # the owner saved it
        for p in board['projects']:
            graph.label(db, [ids[k] for k in p['events'] if k in ids], p['name'], 'review', base.isoformat())
            for i, m in enumerate(p['moves']):
                graph.set_stage(db, p['name'], m['stage'], 'review', base.isoformat(), board=board['name'], stages=board['stages'], reason=m['reason'],
                                event_id=ids[m['event']], next_step=p['next'] if i == len(p['moves']) - 1 else None, org=p['org'])
            if p.get('by_hand'):
                node = 'project:' + str(db.execute('SELECT id FROM projects WHERE name=?', (p['name'],)).fetchone()[0])
                graph.set_stage(db, node, p['by_hand']['stage'], 'owner', stamp(p['by_hand']['days_ago'] * 24 * 60, base))
            db.execute("INSERT OR IGNORE INTO org_profiles(domain,status,profile,fetched_at) VALUES(?,'found',?,?)",
                       (p['org'], json.dumps({'name': p['org_name'], 'industry': 'Software'}), base.isoformat()))
    # Each board has looked through the history already: its header says what that found
    db.execute("UPDATE board_backfills SET state='done',started_at=?,finished_at=?", ((base - dt.timedelta(hours=1)).isoformat(), base.isoformat()))


def load_reviews(db, ids, base, assistant):
    """Reviews ran through the day: every signal has been read."""
    items = [{'id': i, 'version': 1} for i in ids.values()]
    for n, chunk in enumerate(range(0, len(items), 60)):
        at = 60 * (n + 1)
        run_id = f'sandbox-run-{n}'
        db.execute("INSERT INTO review_runs(run_id,status,started_at,updated_at,finished_at,attempts,planned) VALUES(?,'ok',?,?,?,1,?)",
                   (run_id, stamp(at, base), stamp(at - 4, base), stamp(at - 4, base), len(items[chunk:chunk + 60])))
        db.execute("INSERT INTO analyses(id,started_at,finished_at,items,status,summary,owner_session,owner_run) VALUES(?,?,?,?,'complete',?,?,?)",
                   (f'sandbox-analysis-{n}', stamp(at, base), stamp(at - 4, base), json.dumps(items[chunk:chunk + 60]), 'Read and sorted.',
                    assistant.REVIEW_SESSION_PREFIX + run_id, run_id))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dir', default='/tmp/machine-sandbox')
    parser.add_argument('--state', default='busy')
    parser.add_argument('--port', type=int, default=8870)
    args = parser.parse_args()
    config, path = load(args.dir, parse_states(args.state), args.port, args.port + 1)
    print(path)


if __name__ == '__main__':
    main()
