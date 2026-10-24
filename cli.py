#!/usr/bin/env python3
"""Command line for The Machine's personal assistant.

Reads tasks, evidence, sources and review history from the running service,
can save a preparation for an accepted task, runs a first-run preview review and drafts USER.md
lines from your own messages (profile, shown before saving). Sets up
sources (connect) and checks the whole setup (doctor). Owner decisions (accept, snooze,
done, dismiss, feedback) stay with the dashboard and Telegram (optional); the service
refuses them from here.
"""
import argparse
import datetime as dt
import importlib.util
import json
import sqlite3
import os
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location('assistant', Path(__file__).resolve().with_name('assistant.py'))
assistant = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(assistant)
_spec = importlib.util.spec_from_file_location('onboarding', Path(__file__).resolve().with_name('onboarding.py'))
onboarding = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(onboarding)
onboarding._assistant = assistant
import service  # noqa: E402
import support  # noqa: E402

_spec = importlib.util.spec_from_file_location('moving', Path(__file__).resolve().with_name('moving.py'))
moving = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(moving)

TZ = assistant.LOCAL_TZ
GROUPS = {
    'outstanding': ('pending', 'accepted', 'instructions', 'prepared'),
    'later': ('snoozed',),
    'done': ('completed', 'rejected'),
}
LABELS = {'pending': 'Needs your decision', 'accepted': 'Accepted', 'instructions': 'Changes requested',
          'prepared': 'Ready for you', 'snoozed': 'Later', 'completed': 'Done', 'rejected': 'Dismissed'}


class CliError(Exception):
    pass


def load_config(path):
    global TZ
    try:
        config = json.loads(Path(path).read_text())
        if config.get('timezone'):
            TZ = ZoneInfo(config['timezone'])
        return config
    except OSError as error:
        raise CliError(f'Cannot read config {path}: {error.strerror}') from None


def call(config, action, args=None, upload=False):
    if action in assistant.OWNER_ACTIONS:
        raise CliError('Owner decisions and feedback come from Telegram or the dashboard, not the CLI.')
    request = urllib.request.Request(
        f"http://127.0.0.1:{config['port']}/api/action",
        data=json.dumps({'action': action, 'args': args or {}}).encode(),
        headers={'Authorization': 'Bearer ' + assistant.credential(config, 'api-token'),
                 'Content-Type': 'application/json', 'X-Assistant-Client': 'local-cli', **({'X-Upload': 'skill-import'} if upload else {})})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read()).get('error')
        except ValueError:
            message = None
        raise CliError(message or f'Service answered HTTP {error.code}') from None
    except urllib.error.URLError:
        raise CliError('The assistant service is not reachable. Check: assistant service status') from None


def local(value, fmt='%a %d %b %H:%M'):
    if not value:
        return '-'
    try:
        return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(TZ).strftime(fmt)
    except ValueError:
        return str(value)


def loads(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value if value is not None else default


def find_task(status, task_id):
    matches = [s for s in status['suggestions'] if s['id'].startswith(task_id)]
    if not matches:
        raise CliError(f'No task with id {task_id}. List them with: assistant tasks --all')
    if len(matches) > 1:
        raise CliError(f'Id {task_id} is ambiguous: ' + ', '.join(s['id'] for s in matches))
    return matches[0]


def out_json(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def wrap(text, indent='    '):
    return '\n'.join(textwrap.fill(line, 100, initial_indent=indent, subsequent_indent=indent) if line.strip() else ''
                     for line in str(text).splitlines())


def evidence_for(config, task):
    ids = loads(task.get('event_ids'), [])
    accounts = loads(task.get('account_ids'), [])
    if not ids:
        return []
    configured = [a['id'] for a in config['accounts']]
    return call(config, 'events', {'account_ids': [a for a in accounts if a in configured] or configured,
                                   'event_ids': ids, 'limit': max(len(ids), 1), 'full': True})


def print_event(event, full=False):
    meta = loads(event.get('metadata'), {})
    who = meta.get('sender_display') or meta.get('from') or ''
    print(f"  [{event['id']}] {local(event['occurred_at'])} · {event['account_id']} · {event['kind']}" + (f' · {who}' if who else ''))
    print(f"    {event.get('title') or '(no title)'}")
    if meta.get('source_url'):
        print(f"    {meta['source_url']}")
    body = event.get('body') or ''
    if not full and len(body) > 1500:
        body = body[:1500] + f'\n… ({len(event["body"])} characters; use --full for all)'
    print(wrap(body))


def cmd_tasks(config, args):
    status = call(config, 'status', {'full': True})
    groups = ['outstanding', 'later', 'done'] if args.all else [g for g in ('outstanding', 'later', 'done') if getattr(args, g)] or ['outstanding']
    wanted = {s for g in groups for s in GROUPS[g]}
    tasks = sorted((s for s in status['suggestions'] if s['status'] in wanted), key=lambda s: s['created_at'], reverse=True)
    if args.json:
        return out_json(tasks)
    if not tasks:
        print('No tasks in ' + ', '.join(groups) + '.')
        return
    for group in groups:
        rows = [s for s in tasks if s['status'] in GROUPS[group]]
        if not rows:
            continue
        print(f'{group.capitalize()} ({len(rows)})')
        for s in rows:
            extra = f" until {local(s['snoozed_until'])}" if s.get('snoozed_until') else ''
            print(f"  {s['id']}  {LABELS.get(s['status'], s['status'])}{extra} · {local(s['created_at'])}")
            print(f"    {s['title']}")
        print()


def cmd_promises(config, args):
    promises = call(config, 'status', {'full': True}).get('promises', [])
    promises = [p for p in promises if args.all or p['state'] in ('open', 'card')]
    if args.json:
        return out_json(promises)
    if not promises:
        print('No open promises.' if not args.all else 'No promises in the last two weeks.')
        return
    for direction, heading in (('mine', 'You promised'), ('theirs', 'Promised to you')):
        rows = [p for p in promises if p['direction'] == direction]
        if not rows:
            continue
        print(f'{heading} ({len(rows)})')
        for p in rows:
            due = f" · due {dt.date.fromisoformat(p['due']):%a %d %b}" if p['due'] else ''
            state = {'card': ' · card out', 'kept': ' · kept', 'dropped': ' · dropped', 'answered': ' · they wrote again'}.get(p['state'], '')
            print(f"  #{p['id']}  {p['who']}: {p['what']}{due}{state}")
            print(f"    said {local(p['made_at'])}" + (f" · {p['note']}" if p.get('note') and p['state'] != 'open' else ''))
        print()


def cmd_show(config, args):
    status = call(config, 'status', {'full': True})
    task = find_task(status, args.id)
    decisions = [d for d in status['decisions'] if d['suggestion_id'] == task['id']]
    evidence = [] if args.no_evidence else evidence_for(config, task)
    if args.json:
        return out_json({'task': task, 'decisions': decisions, 'evidence': evidence})
    print(f"{task['title']}\n  {task['id']} · {LABELS.get(task['status'], task['status'])} · created {local(task['created_at'])}"
          + (f" · until {local(task['snoozed_until'])}" if task.get('snoozed_until') else ''))
    print(f"  Accounts: {', '.join(loads(task.get('account_ids'), []))}\n")
    print(wrap(task['body'], '  '))
    if task.get('latest_feedback'):
        print('\nYou asked:\n' + wrap(task['latest_feedback'], '  '))
    if decisions:
        print('\nDecisions (recent):')
        for d in sorted(decisions, key=lambda d: d['created_at']):
            print(f"  {local(d['created_at'])} {d['choice']} via {d['source']}" + (f": {d['feedback']}" if d.get('feedback') else ''))
    if evidence:
        print(f'\nEvidence ({len(evidence)}):')
        for event in evidence:
            print_event(event, args.full)
            print()


def cmd_search(config, args):
    accounts = args.account or [a['id'] for a in config['accounts']]
    events = call(config, 'events', {'account_ids': accounts, 'query': args.query, 'limit': args.limit, 'full': True})
    if args.json:
        return out_json(events)
    if not events:
        print('Nothing found.')
    for event in events:
        print_event(event, args.full)
        print()


def cmd_events(config, args):
    request = {'account_ids': args.account, 'limit': args.limit, 'full': True}
    if args.ids:
        request['event_ids'] = [int(i) for i in args.ids.split(',') if i.strip()]
        request['limit'] = max(args.limit, len(request['event_ids']))
    if args.query:
        request['query'] = args.query
    events = call(config, 'events', request)
    if args.json:
        return out_json(events)
    for event in events:
        print_event(event, args.full)
        print()


def cmd_attachment(config, args):
    if args.open:
        return out_json(call(config, 'mail_attachment', {'open': str(Path(args.open).expanduser().resolve()), 'job_id': args.job}))
    request = {'event_id': args.event} if args.event is not None else {'account_id': args.account, 'message_id': args.message}
    if args.save:
        request.update(save=args.save, job_id=args.job)
    result = call(config, 'mail_attachment', request)
    if args.json or args.save:
        return out_json(result)
    for a in result['attachments']:
        print(f"{a['name']}  ({a['mime']}, {a['size']:,} bytes, id {a['id']})" + (f" - {a['note']}" if a.get('note') else ''))
    if not result['attachments']:
        print('No attachments.')


def cmd_sources(config, args):
    connectors = call(config, 'status', {'full': True})['connectors']
    if args.json:
        return out_json(connectors)
    for c in connectors:
        state = 'ERROR ' + str(c['error']) if c.get('error') else 'ok'
        print(f"  {c['id']:<26} {c.get('service', ''):<11} {state:<10} last sync {local(c.get('last_success'))} · "
              f"{c.get('signals_24h', 0)} in 24h · {c.get('pending', 0)} not reviewed · {c.get('failures_24h', 0)} failures in 24h")


def cmd_reviews(config, args):
    status = call(config, 'status', {'full': True})
    analyses = sorted(status['analyses'], key=lambda a: a['started_at'], reverse=True)[:args.limit]
    runs = status.get('review_runs',[])[:args.limit]
    if args.json:
        return out_json({'review_health': status['review_health'], 'analyses': analyses, 'runs':runs})
    health = status['review_health']
    run = health.get('worker') or {}
    state = ('waiting for Gateway recovery; retry ' + local(run.get('retry_at'))) if run.get('status') == 'recovering' else \
            ('running since ' + local(run['started_at'])) if run.get('status') in ('starting', 'running') else \
            (f"last run {run['status']} at {local(run.get('finished_at'))}" if run else 'no run yet')
    aside = f" ({health['set_aside']} set aside)" if health.get('set_aside') else ''
    print(f"Backlog: {health.get('pending')} of {health.get('total')} signals not reviewed{aside}. "
          f"Last review saved {local(health.get('last_completed'))}. Review worker: {state}.\n")
    for a in analyses:
        print(f"  {local(a['started_at'])} {a['status']:<10} {len(loads(a['items'], []))} signals · {a.get('summary') or ''}"[:160])
    if runs:
        print('\nRecent runs:')
        for r in runs:
            outcome = f"{r['checked_count']} signal versions saved" if r.get('review_saved') else f"{r.get('pending_count',0)} still waiting" if r.get('pending_count') else 'no waiting signals from this run'
            error = r.get('error') or r.get('last_error')
            phase = r.get('error_phase') or r.get('last_error_phase')
            print(f"  {local(r['started_at'])} {r['status']} · {outcome} · {r.get('duration_seconds',0)}s"+
                  (f" · {error} ({phase or 'runtime'})" if error else ''))


def cmd_feedback(config, args):
    status = call(config, 'status', {'full': True})
    titles = {s['id']: s['title'] for s in status['suggestions']}
    decisions = sorted(status['decisions'], key=lambda d: d['created_at'], reverse=True)[:args.limit]
    if args.json:
        return out_json(decisions)
    for d in decisions:
        print(f"  {local(d['created_at'])} {d['choice']:<9} via {d['source']:<9} {titles.get(d['suggestion_id'], d['suggestion_id'])}"
              + (f"\n      “{d['feedback']}”" if d.get('feedback') else ''))


def cmd_prepare(config, args):
    body = args.body if args.body is not None else (sys.stdin.read() if args.file == '-' else Path(args.file).read_text())
    task = find_task(call(config, 'status', {'full': True}), args.id)
    result = call(config, 'prepare', {'suggestion_id': task['id'], 'body': body.strip(),
                                   **({'next_action':json.loads(Path(args.next_action).read_text())} if args.next_action else {})})
    if args.json:
        return out_json(result)
    print(f"Saved as Ready for you: {task['title']}. The Telegram card is updated and you'll get one notification.")


PREVIEW_POLL = 10


def dashboard_url(config):
    return f"https://{config['dashboard_host']}{config.get('dashboard_path', '/')}" if config.get('dashboard_host') else None


def preview_cards(status, run):
    # Cards this preview saved: shown only (delivery 'previewed'), created during the run.
    shown = {d['suggestion_id'] for d in status.get('deliveries', []) if d['status'] == 'previewed'}
    return sorted((s for s in status['suggestions'] if s['id'] in shown and s['created_at'] >= run['started_at']), key=lambda s: s['created_at'])


def preview_progress(run, cards):
    elapsed = int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(run['started_at'])).total_seconds())
    phase = {'starting': 'starting the review in OpenClaw', 'recovering': 'OpenClaw is not answering; waiting for it'}.get(
        run['status'], f"reviewing {run['claimed_count']} signals" if run.get('claimed_count') else 'your agent is reading the signals')
    return f"  {elapsed // 60}m {elapsed % 60:02d}s · {phase} · {len(cards)} suggestion{'s' if len(cards) != 1 else ''} so far"


def cmd_preview(config, args):
    started = call(config, 'preview_review')
    if not args.json:
        print('A preview is already running; following it.' if started.get('attached') else
              f"Reviewing {started['signals']} signals from the last {assistant.PREVIEW_DAYS} days with your OpenClaw agent"
              + (f" ({started['waiting']} are waiting; the rest are reviewed once activated)" if started['waiting'] > started['signals'] else '') + '.')
        print('Nothing is sent to Telegram and nothing is done in your accounts. This usually takes a few minutes.\n')
    settings = config.get('review_worker') or {}
    # The service stops a run after timeout + outage grace; wait a little longer for its verdict.
    deadline = time.monotonic() + settings.get('timeout_seconds', 1500) + settings.get('outage_grace_seconds', 600) + 120
    tty, last = sys.stdout.isatty() and not args.json, None
    try:
        while True:
            status = call(config, 'status', {'full': True})
            run = next((r for r in status.get('review_runs', []) if r['run_id'] == started['run_id']), None)
            if not run:
                raise CliError('The preview run is missing from the service status; see assistant reviews.')
            cards = preview_cards(status, run)
            if run['status'] in assistant.REVIEW_TERMINAL:
                break
            line = preview_progress(run, cards)
            if tty:
                print('\r' + line.ljust(78), end='', flush=True)
            elif not args.json and line.split(' · ', 1)[1] != last:
                print(line, flush=True)
                last = line.split(' · ', 1)[1]
            if time.monotonic() > deadline:
                raise CliError('The preview is still running. It continues in the service: run assistant preview again to follow it.')
            time.sleep(PREVIEW_POLL)
    except KeyboardInterrupt:
        print('\nStopped following. The review keeps running in the service; run assistant preview again to follow it.')
        return
    finally:
        if tty:
            print()
    if args.json:
        return out_json({'run': run, 'cards': cards})
    failed = run['status'] != 'ok' or not run.get('review_saved')
    if cards:
        print(f"{len(cards)} suggestion{'s' if len(cards) != 1 else ''} from your recent signals:\n")
        for n, card in enumerate(cards, 1):
            print(f"{n}. {card['title'].replace('**', '')}" + (f"  (due {card['due']})" if card.get('due') else ''))
            print(wrap(card['body'].replace('**', ''), '   '))
            if card.get('source_line'):
                print('   ' + card['source_line'])
            print()
    elif not failed:
        print(f"The review checked {run.get('checked_count', 0)} signals and found nothing worth your time right now. "
              'That happens on a quiet week; regular reviews will keep looking once activated.\n')
    dashboard = dashboard_url(config)
    print('These are real suggestions from your own signals. Nothing was sent and no action was taken in any account.')
    print('Reviews and Telegram delivery are still off. ' + (f'The cards are on the dashboard: {dashboard} ' if dashboard else '')
          + "(assistant tasks lists them too); they won't be sent to Telegram later.")
    print('When you want this every day, with Telegram cards and Do it: ./install.sh --activate')
    if failed:
        error = run.get('error') or run.get('last_error') or ('ended without saving its review' if run['status'] == 'ok' else run['status'])
        raise CliError(f"The preview review didn't finish ({error}). See assistant reviews and openclaw logs, then run assistant preview again.")


def cmd_profile(config, args):
    path = onboarding.user_md_path(args.workspace)
    current = path.read_text() if path.exists() else onboarding.starter_user_md(config)
    if onboarding.user_md_is_template(path):
        current = current.rstrip() + '\n\n' + onboarding.starter_user_md(config)
    room = onboarding.profile_room(current)
    started = call(config, 'profile_start', {'again': bool(args.again), 'room': max(room, 0)})
    if not args.json:
        if room < 400:
            print(f'USER.md already uses {len(onboarding.without_profile(current)):,} of the {onboarding.USER_MD_BUDGET:,} characters OpenClaw chats load,'
                  ' so only the most useful lines of the draft fit. Make room in USER.md, or keep everything with --all.\n')
        counts = started.get('counts') or {}
        print('Following the profile draft that is already running.' if started.get('attached') else
              'Your OpenClaw agent reads ' + ', '.join(f'{n} {k}' for k, n in sorted(counts.items(), key=lambda c: -c[1]))
              + ' of your own recent messages once and drafts how you write, what matters to you and what your closest people are to you.')
        print('Nothing is sent. Roles go into the graph, where you can change them; USER.md only changes when you say yes. Usually a few minutes.\n')
    deadline, tty = time.monotonic() + assistant.PROFILE_SECONDS + 1500, sys.stdout.isatty() and not args.json
    try:
        while True:
            run = call(config, 'profile_status', {'run_id': started['run_id']})
            if run['status'] not in ('starting', 'running'):
                break
            if time.monotonic() > deadline:
                raise CliError('The profile draft is still running. Run assistant profile again to pick it up.')
            if tty:
                elapsed = int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(run['started_at'])).total_seconds())
                print(f"\r  {elapsed // 60}m {elapsed % 60:02d}s · " + ('starting in OpenClaw' if run['status'] == 'starting' else 'your agent is reading'),
                      end='', flush=True)
            time.sleep(5)
    except KeyboardInterrupt:
        print('\nStopped following. The draft keeps going in the service; run assistant profile again to see it.')
        return
    finally:
        if tty:
            print()
    if run['status'] != 'ok' or not run.get('draft'):
        raise CliError(f"The profile draft didn't finish ({run.get('error') or run['status']}). See openclaw logs, then run assistant profile --again.")
    if args.json:
        return out_json(run)
    save_draft(config, args, path, run)
    if started.get('roles_run_id'):
        print()
        follow_roles(config, started['roles_run_id'])


def save_draft(config, args, path, run):
    day = dt.datetime.now(TZ).date().isoformat()
    while True:
        # The owner can edit USER.md while inference or the save prompt is running.
        observed = path.read_text() if path.exists() else None
        current = observed if observed is not None else onboarding.starter_user_md(config)
        if onboarding.user_md_is_template(path):
            current = current.rstrip() + '\n\n' + onboarding.starter_user_md(config)
        if args.all:
            merged, left_out = onboarding.merge_profile(current, run['draft'], day), []
        else:
            merged, left_out = onboarding.fit_profile(current, run['draft'], day)
        print(merged[merged.index(onboarding.PROFILE_HEADING):] if merged else onboarding.render_profile(run['draft'], day))
        if left_out:
            print(f'Left out to stay within the {onboarding.USER_MD_BUDGET:,} characters OpenClaw chats load (keep them with --all, or make room in USER.md):')
            print('\n'.join(f'  - {line}' for line in left_out) + '\n')
        if merged is None:
            print(f'{path} already has an "{onboarding.PROFILE_HEADING}" section you made your own, so it stays as it is. Copy what you like from above.')
            return
        if not (args.yes or (sys.stdin.isatty() and input(f'Add this to {path}? You can edit it afterwards [Y/n]: ').strip().lower() in ('', 'y', 'yes'))):
            print('Not saved. Run assistant profile again any time.')
            return
        if (path.read_text() if path.exists() else None) != observed:
            print('USER.md changed meanwhile. Keeping those edits and refreshing the draft.\n')
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        onboarding.write_private(path, merged)
        break
    print(f'Saved to {path}. Reviews and drafts read it from the next turn on.'
          + (f' It is now {len(merged):,} characters; OpenClaw chat sessions load the first {onboarding.USER_MD_BUDGET:,}.'
             if len(merged) > onboarding.USER_MD_BUDGET else ''))


def follow_roles(config, run_id, quiet=False):
    """Follow a roles turn and list what it named; it runs in the service either way."""
    if not quiet: print('Now your agent reads the first and latest messages with your closest people and notes what each, and their organization, is to you.')
    deadline, tty = time.monotonic() + assistant.PROFILE_SECONDS + 1500, (sys.stdout.isatty() and not quiet)
    try:
        while True:
            run = call(config, 'profile_status', {'run_id': run_id})
            if run['status'] not in ('starting', 'running'):
                break
            if time.monotonic() > deadline:
                raise CliError('Still naming roles. They show up in the dashboard\'s Graph tab when done.')
            if tty:
                elapsed = int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(run['started_at'])).total_seconds())
                print(f"\r  {elapsed // 60}m {elapsed % 60:02d}s · " + ('starting in OpenClaw' if run['status'] == 'starting' else 'your agent is reading'),
                      end='', flush=True)
            time.sleep(5)
    except KeyboardInterrupt:
        if not quiet: print('\nStopped following. Roles keep coming in the service; see the dashboard\'s Graph tab.')
        return None
    finally:
        if tty:
            print()
    if run['status'] != 'ok':
        raise CliError(f"Naming roles didn't finish ({run.get('error') or run['status']}). See openclaw logs, then run assistant roles --again.")
    if not quiet and run.get('roles'):
        print('What your closest people and organizations are to you (confirm or change them in Who\'s who, in the dashboard\'s Graph tab):')
        print('\n'.join(f"  - {r['name']}: {r['role']}" for r in run['roles']))
    elif not quiet:
        print('Their messages didn\'t make it clear for anyone yet. Reviews keep asking as more comes in.')
    return run


def cmd_roles(config, args):
    started = call(config, 'roles_start', {'again': bool(args.again)})
    if not started.get('run_id'):
        if args.json: return out_json(started)
        print('Everyone you are close to already has a role. See the dashboard\'s Graph tab.')
        return
    run = follow_roles(config, started['run_id'], quiet=args.json)
    if args.json and run:
        return out_json(run)


def cmd_enrich(config, args):
    if args.domain:
        found = call(config, 'enrich', {'domain': args.domain})
        if args.json:
            return out_json(found)
        profile = found['profile']
        if not profile:
            print(f"The enrichment service doesn't know {found['domain']}.")
            return
        print(f"{profile.get('name') or found['domain']} ({found['domain']})")
        for label, key in (('Industry', 'industry'), ('Size', 'size'), ('Employees', 'employees'), ('Location', 'location'), ('Founded', 'founded'),
                           ('Type', 'type'), ('LinkedIn', 'linkedin_url'), ('Website', 'website'), ('About', 'about')):
            if profile.get(key):
                print(f'  {label + ":":<11}{profile[key]}')
        return
    state = call(config, 'enrich', {})
    if args.json:
        return out_json(state)
    people = state.get('people') or {}
    people_line = (f"Work contacts: {people.get('found', 0)} found, {people.get('unknown', 0)} unknown"
                   + ('. Look up work contacts is off in Settings.' if people.get('on') is False else '.'))
    if not state['service']:
        print('Enrichment is off. Add "enrichment" to the config to look up the organizations you deal with (see README, Enrichment).')
        if people.get('found') or people.get('unknown'):
            print(people_line)
        return
    print(f"Organization data from {state['service']}: {state['found']} found, {state['unknown']} unknown"
          + (f", {state['errors']} errors (last {state['last_error_at'][:16].replace('T', ' ')})" if state['errors'] else '')
          + f". Each is looked up again after {state['refresh_days']} days.")
    if people:
        print(people_line)


def cmd_connect(config, args):
    if not args.source:
        if args.json:
            return out_json([{'id': a['id'], 'service': a.get('service', 'gmail'), 'missing': onboarding.missing(config, a)}
                             for a in config.get('accounts', [])])
        print('Sources: ' + ', '.join(onboarding.sources()) + '\n')
        for account in config.get('accounts', []):
            reason = onboarding.missing(config, account)
            print(f"  {account['id']:<26} {onboarding.label(account.get('service', 'gmail')):<22} "
                  + (f'not set up ({reason}): assistant connect {onboarding.connect_name(account, config)}' if reason else 'set up'))
        found = onboarding.discover(config)
        if found:
            print('\nOn this machine, not connected yet:')
            for what, command in found:
                print(f'  {what}: {command}')
        print('\nConnect, redo or add one: assistant connect <source> [--account <id>]')
        return
    if not sys.stdin.isatty():
        raise CliError('assistant connect asks a few questions; run it in a terminal.')
    try:
        onboarding.connect(args.config, args.source, onboarding.UI(), args.account)
    except onboarding.SetupError as error:
        raise CliError(error.message + (f'\n  fix: {error.fix}' if error.fix else '')) from None
    except (KeyboardInterrupt, EOFError):
        raise CliError('stopped; nothing was saved to config.json') from None
    except Exception as error:  # say what broke instead of a traceback; config.json is only written after a good read
        raise CliError(f'connecting {args.source} failed unexpectedly: {type(error).__name__}: {str(error)[:300]}\n'
                       '  config.json was not changed; try again, or see what else is off with: assistant doctor') from None


def cmd_doctor(config, args):
    results = onboarding.doctor(args.config)
    if args.json:
        out_json([{'status': {'✓': 'ok', '!': 'warn', '✗': 'fail'}[m], 'check': t, 'fix': f} for m, t, f in results])
        sys.exit(1 if any(m == '✗' for m, *_ in results) else 0)
    print('The Machine: doctor\n')
    sys.exit(onboarding.print_doctor(results, onboarding.UI(False)))


def cmd_telegram(config, args):
    if args.state == 'status':
        owner = str(config.get('telegram_owner') or '')
        on = {'telegram': bool(owner), 'owner': owner or None, 'account': config.get('telegram_account') or ('default' if owner else None)}
        return out_json(on) if args.json else print(f"Telegram on: cards go to {owner} through the {on['account']} bot." if owner else
                                                    'Telegram off: cards reach you through the dashboard app (add it to your home screen, turn on notifications).')
    if args.state == 'on' and not args.owner:
        raise CliError('give your Telegram user ID: assistant telegram on 123456789')
    try:
        notes = onboarding.set_telegram(args.config, args.owner if args.state == 'on' else None, remove_bot=args.remove_bot)
    except onboarding.SetupError as error:
        raise CliError(error.message + (f'\n  fix: {error.fix}' if error.fix else '')) from None
    if args.json:
        return out_json({'telegram': args.state == 'on', 'notes': notes})
    print('Telegram on: new cards go to Telegram again, next to the dashboard app.' if args.state == 'on' else
          'Telegram off: cards, reminders and the weekly recap reach you through the dashboard app.'
          ' Add it to your home screen and turn on notifications (Settings, the bell). Back on any time: assistant telegram on <your Telegram user ID>')
    for note in notes:
        print('  ' + note)


def hours_text(hours):
    days = assistant.WORK_HOURS_DEFAULT['days'] if not hours.get('days') else hours['days']
    names = ', '.join(assistant.DAY_NAMES[d - 1].capitalize() for d in days)
    return (f"Work days: {names}\nHours: {hours['start']}–{hours['end']} (nothing is sent outside them)\n"
            f"Snoozes and returning threads land at {hours['morning']}; morning digest at {hours['digest_at']}\n"
            f"Time zone: {hours['timezone']}")


def cmd_hours(config, args):
    changes = {key: value for key, value in (('days', args.days), ('start', args.start), ('end', args.end), ('morning', args.morning),
                                             ('digest_at', args.digest), ('timezone', args.timezone)) if value}
    try:
        values = assistant.hours_settings(config, changes)
    except ValueError as error:
        raise CliError(str(error)) from None
    if changes:
        assistant.save_hours(args.config, values)
        if not args.no_restart and service.available():
            service.restart(service.UNIT_NAME, run=onboarding.run)
    hours = {**values['work_hours'], 'timezone': values['timezone'], 'digest_at': values['digest_at']}
    if args.json:
        return out_json(hours)
    print(('Saved. ' if changes else '') + hours_text(hours))
    if not changes:
        print('Change with e.g.: assistant hours --days mon-fri --start 08:00 --end 23:00 --morning 09:00 (or on the dashboard: Settings, the gear at the top)')


def _passphrase(confirm):
    import getpass
    if os.environ.get(moving.PASSPHRASE_ENV):
        return os.environ[moving.PASSPHRASE_ENV]
    if not sys.stdin.isatty():
        raise CliError(f'give the {"passphrase" if confirm else "export code or passphrase"} in {moving.PASSPHRASE_ENV}, or run this in a terminal')
    value = getpass.getpass('Passphrase: ' if confirm else 'Export code or passphrase: ')
    if confirm and getpass.getpass('Same passphrase again: ') != value:
        raise CliError('the two passphrases differ; nothing was written')
    return value


def cmd_export(config, args):
    try:
        if args.estimate:
            out = moving.estimate(args.config, secrets=args.keys)
            if args.json:
                return out_json(out)
            print(f'{out["files"]} files, about {out["bytes"] / 1e6:.1f} MB before compression')
            if out['left_out']:
                print('Left out (a computer rebuilds these): ' + moving.left_out_text(out['left_out']))
            return
        path, manifest = moving.export(args.config, args.out, passphrase=_passphrase(True) if args.keys else None)
    except moving.MoveError as error:
        raise CliError(str(error)) from None
    if args.json:
        return out_json({'file': str(path), 'bytes': path.stat().st_size, 'includes_keys': manifest['includes_keys'],
                         'left_out': manifest['left_out'], 'network_identity': manifest.get('network_identity'),
                         **({'code': manifest['code']} if manifest.get('code') else {})})
    print(f'Wrote {path} ({path.stat().st_size / 1e6:.1f} MB), encrypted' + (', keys included' if args.keys else ', without keys'))
    if manifest.get('code'):
        print(f'Export code: {manifest["code"]}\nSave it now: the import asks for it, and it is not kept anywhere.')
    if manifest.get('network_identity') == 'new':
        print('Without keys, a new Network identity will be made after the import; teammates and friends see a '
              'key-change notice once. Export with --keys to keep it.')
    if manifest['left_out']:
        print('Left out (a computer rebuilds these): ' + moving.left_out_text(manifest['left_out']))
    print('Put it back on another computer with:  assistant import ' + path.name)


def cmd_import(config, args):
    try:
        result = moving.import_(args.archive, args.config, passphrase=_passphrase(False) if args.archive.endswith('.enc') else None,
                                force=args.force)
    except moving.MoveError as error:
        raise CliError(str(error)) from None
    if args.json:
        return out_json(result)
    print(f'Restored {len(result["restored"])} files.')
    for note in result['notes']:
        print('  note: ' + note)
    print('\nConnect again:')
    for line in result['reconnect']:
        print('  - ' + line)
    print('\nThen start the service (assistant service start) and check with: assistant doctor')


def cmd_fleet_pair(config, args):
    try:
        found = assistant.fleet.pair(args.code, args.label or os.uname().nodename, assistant.fleet.account_emails(config))
    except (ValueError, OSError) as error:
        raise CliError(f'pairing failed: {error}') from None
    path = Path(config.get('database') or assistant.DEFAULT_CONFIG).expanduser().parent / 'fleet-token'
    onboarding.write_private(path, found['token'] + '\n')
    saved = onboarding.load(args.config)
    saved.pop('fleet_relay_url', None)  # from before the relay was built in
    saved.setdefault('credentials', {})['fleet'] = str(path)
    onboarding.save(args.config, saved)
    if args.json:
        return out_json({'paired': True, 'label': found.get('label'), 'token_file': str(path)})
    print(f'Paired as "{found.get("label")}". Its key is in {path} (only you can read it).\n'
          'Restart the service so it joins your Fleet; pick this Machine for a Fleet in your account there.')


def cmd_skills(config, args):
    view = call(config, 'status', {}).get('skills') or {'skills': []}
    if args.json:
        return out_json(view)
    if not view['skills']:
        print('No skills yet. Write one on the dashboard: Settings, Skills.')
    for skill in view['skills']:
        state = {'on': 'on', 'off': 'off', 'proposed': 'proposed, waits for you', 'incoming': f"from {skill['author']}, waits for you"}[skill['status']]
        print(f"{skill['name']}  ({state})\n  {skill['description']}")


def cmd_skills_propose(config, args):
    body = sys.stdin.read() if args.file == '-' else Path(args.file).read_text()
    out = call(config, 'skill_propose', {'name': args.name, 'description': args.description, 'body': body, 'why': args.why or ''})
    if args.json:
        return out_json(out)
    print('Already proposed or on.' if out.get('duplicate') else 'Proposed: it waits for the owner\'s Approve on the dashboard (Settings, Skills).')


def cmd_skills_found(config, args):
    """The Find my skills turn's report: the owner's account skills it read and the ones it recommends."""
    report = json.loads(sys.stdin.read() if args.file == '-' else Path(args.file).read_text())
    if not isinstance(report, dict):
        raise CliError('The report is a JSON object: {"found": [...], "recommended": [...], "browser": {...}}')
    out = call(config, 'skill_import_report', {**report, 'run': args.run}, upload=True)
    if args.json:
        return out_json(out)
    print(f"Reported: {out.get('added', 0)} skill(s) from the owner's accounts added to the list.")


def cmd_task_import(config, args):
    """A task link someone sent by email: adding it is the owner's own step, so it opens on the dashboard."""
    invite_id, key = assistant.task_invites.parse_link(args.link)
    root = dashboard_url(config) or 'http://127.0.0.1:' + str(config.get('port', 8787)) + '/'
    print(f'Open this on your dashboard to add the task: {root}#task={invite_id}.{key}')


def cmd_service(config, args):
    """The background service, the same on Linux (systemd) and macOS (launchd)."""
    if not service.available():
        raise CliError(f'no service manager here ({service.manager()}); run it yourself: python3 {assistant.ROOT / "assistant.py"} serve')
    if args.action == 'logs':
        shown = service.logs(lines=args.lines, follow=args.follow, run=onboarding.run)
        if isinstance(shown, str):
            print(shown)
        return
    if args.action == 'status':
        print(service.status_text(run=onboarding.run))
        return
    result = {'start': service.start, 'stop': service.stop, 'restart': service.restart}[args.action](service.UNIT_NAME, run=onboarding.run)
    if result.returncode:
        raise CliError(f'{args.action} failed: ' + ((result.stderr or result.stdout or '').strip()[:300] or 'no details')
                       + f'\n  see: assistant service logs')
    print({'start': 'Started.', 'stop': 'Stopped. Start it again with: assistant service start', 'restart': 'Restarted.'}[args.action])


def cmd_message_key(config, args):
    """Where the message key is kept: its file next to the database, or the desktop's keyring (message_keyring.py)."""
    import message_keyring
    database = Path(config.get('database') or '').expanduser()
    if not config.get('database'):
        raise CliError('config.json has no database')
    try:
        if args.action == 'status':
            found = message_keyring.status(database)
            if args.json:
                out_json(found)
            else:
                for name, value in found.items():
                    print(f'{name}: {value}')
        elif args.action == 'to-keyring':
            message_keyring.to_keyring(database)
        else:
            message_keyring.to_file(database, keep_in_keyring=args.keep_in_keyring)
    except message_keyring.Unavailable as error:
        raise CliError(f'nothing changed: {error}') from None
    except assistant.message_seal.SealError as error:
        raise CliError(f'nothing changed: {error}') from None


def cmd_support_bundle(config, args):
    path = support.bundle(args.config, out=args.out, doctor=onboarding.doctor)
    print(f'Saved {path}\nIt holds versions, doctor, the service and Gateway logs and config.json without keys, tokens,\n'
          'passwords or addresses. Look through it, then send it to whoever helps you.')


def cmd_raw(config, args):
    out_json(call(config, args.action, json.loads(args.args) if args.args else {}))


def cmd_busy(config, args):
    """Work a restart or an update would cut off, read straight from the database (also while the service is down).
    Exit status 0 when nothing runs, 3 when something does: port and update scripts rely on this state, not on age."""
    path = Path(config['database']).expanduser()
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=15)
        db.execute('PRAGMA busy_timeout=15000')
        found = assistant.active_work(db)
        db.close()
    except sqlite3.Error as error:
        raise CliError(f'Cannot read {path}: {error}') from None
    busy = sum(len(rows) for rows in found.values())
    if args.json:
        out_json({'busy': busy, **found})
    elif not busy:
        print('Nothing running.')
    else:
        for name, rows in found.items():
            for row in rows:
                print(f"  {name:<13} {row['status']:<18} {row['id']} (task {row['suggestion_id']})")
    if busy:
        sys.exit(3)


def parser():
    p = argparse.ArgumentParser(prog='assistant', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog='Examples:\n  assistant tasks\n  assistant show 7b61\n  assistant search "Acme"\n'
                                       '  assistant promises\n  assistant prepare 7b61 --file brief.md\n  assistant preview\n  assistant connect gmail\n  assistant doctor')
    p.add_argument('--config', default=str(assistant.DEFAULT_CONFIG), help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest='command', required=True, metavar='command')

    def add(name, help_text, func):
        command = sub.add_parser(name, help=help_text, description=help_text)
        command.add_argument('--json', action='store_true', help='machine-readable output')
        command.set_defaults(func=func)
        return command

    c = add('tasks', 'List tasks (default: Outstanding, as on the dashboard)', cmd_tasks)
    c.add_argument('--later', action='store_true', help='snoozed tasks')
    c.add_argument('--done', action='store_true', help='done and dismissed tasks')
    c.add_argument('--outstanding', action='store_true', help=argparse.SUPPRESS)
    c.add_argument('--all', action='store_true', help='every task')
    c = add('promises', 'Promises you made and promises made to you, from your messages', cmd_promises)
    c.add_argument('--all', action='store_true', help='also those closed in the last two weeks')
    c = add('show', 'Show one task with its decisions and evidence', cmd_show)
    c.add_argument('id', help='task id or a unique prefix')
    c.add_argument('--no-evidence', action='store_true')
    c.add_argument('--full', action='store_true', help='do not shorten long evidence')
    c = add('search', 'Full-text search over collected signals', cmd_search)
    c.add_argument('query')
    c.add_argument('--account', action='append', help='limit to an account id (repeatable; default all)')
    c.add_argument('--limit', type=int, default=20)
    c.add_argument('--full', action='store_true')
    c = add('events', 'Read signals from explicit accounts, by id or query', cmd_events)
    c.add_argument('--account', action='append', required=True, help='account id (repeatable)')
    c.add_argument('--ids', help='comma-separated event ids')
    c.add_argument('--query')
    c.add_argument('--limit', type=int, default=20)
    c.add_argument('--full', action='store_true')
    c = add('attachment', "An email's attachments (Gmail or Outlook), read-only: list them, or save one for a preparation", cmd_attachment)
    c.add_argument('--event', type=int, help="the collected email's event id")
    c.add_argument('--account', help='Gmail or Outlook account id (with --message)')
    c.add_argument('--message', help='message id (with --account)')
    c.add_argument('--save', help='attachment id or name to save')
    c.add_argument('--job', help='the preparation job id: saves under its attachments folder')
    c.add_argument('--open', help='a readable copy of an encrypted attachment saved earlier (with --job)')
    add('sources', 'Connection health per source', cmd_sources)
    add('busy', 'What is running now (agent jobs, sends, preparations); exit status 3 while something runs, for scripts', cmd_busy)
    c = add('reviews', 'Review backlog and recent review runs', cmd_reviews)
    c.add_argument('--limit', type=int, default=10)
    c = add('feedback', 'Recent owner decisions and written feedback', cmd_feedback)
    c.add_argument('--limit', type=int, default=20)
    c = add('prepare', 'Save a short preparation (max 1000 chars) on an accepted task', cmd_prepare)
    c.add_argument('id')
    c.add_argument('--next-action', help='JSON file containing an exact email draft or original-page link; prepares only')
    source = c.add_mutually_exclusive_group(required=True)
    source.add_argument('--body')
    source.add_argument('--file', help='file with the text, or - for stdin')
    add('preview', f'First run: one review of the last {assistant.PREVIEW_DAYS} days, shown here and on the dashboard; nothing is sent', cmd_preview)
    c = add('enrich', 'Organization and work contact data from your enrichment service: status, or look up one domain now', cmd_enrich)
    c.add_argument('domain', nargs='?', help='a domain to look up now, like globex.com')
    c = add('roles', 'Note what your closest people and organizations are to you, from your messages with them (also part of profile)', cmd_roles)
    c.add_argument('--again', action='store_true', help='run again even if it just ran')
    c = add('profile', 'Draft how you write and what matters to you from your own messages, for USER.md (you see it first), then note what your closest people are to you', cmd_profile)
    c.add_argument('--again', action='store_true', help='make a new draft even if one was just made')
    c.add_argument('--yes', action='store_true', help='save the draft without asking')
    c.add_argument('--all', action='store_true', help='keep every line, even past the 4,000 characters OpenClaw chats load')
    c.add_argument('--workspace', help='OpenClaw workspace with USER.md (default: OPENCLAW_WORKSPACE or ~/.openclaw/workspace)')
    c = add('connect', 'Set up a source: sign-in or key, IDs, a test read and the first sync', cmd_connect)
    c.add_argument('source', nargs='?', help='gmail, calendar, outlook, outlook_calendar, slack, crm, whatsapp, android, gateway or a connector plugin')
    c.add_argument('--account', help='account id, to redo one of several or add another')
    add('doctor', 'Check the whole setup; every problem comes with its fix', cmd_doctor)
    c = add('telegram', 'Telegram is optional: turn card delivery there off or back on (the dashboard app works either way)', cmd_telegram)
    c.add_argument('state', choices=('status', 'on', 'off'))
    c.add_argument('owner', nargs='?', help='with on: your Telegram user ID')
    c.add_argument('--remove-bot', action='store_true', help='with off: also delete the Telegram bot and its token from OpenClaw')
    c = add('hours', 'Your work hours: work days, when The Machine may message you, when snoozes land, the digest and time zone', cmd_hours)
    c.add_argument('--days', help='work days, like mon-fri, sun-thu or mon,tue,thu')
    c.add_argument('--start', help='messages from this time, like 08:00')
    c.add_argument('--end', help='until this time, like 23:00 (may be past midnight)')
    c.add_argument('--morning', help='when Tomorrow, 3 days and 1 week snoozes land, like 09:00')
    c.add_argument('--digest', help='morning digest time, like 08:30')
    c.add_argument('--timezone', help='IANA time zone, like Europe/Brussels')
    c.add_argument('--no-restart', action='store_true', help='save only; the service picks it up at its next restart')
    c = add('export', 'Everything The Machine knows and how it is set up, in one encrypted archive to move to another computer', cmd_export)
    c.add_argument('--keys', action='store_true', help='include keys and tokens, with a passphrase you pick (without: an export code is made for you)')
    c.add_argument('--out', default='.', help='folder to write the archive to (default: here)')
    c.add_argument('--estimate', action='store_true', help='only say how big it would be')
    c = add('import', 'Put an export back on this computer (a fresh install)', cmd_import)
    c.add_argument('archive', help='the .tar.gz.enc file (older .zip and .tar.gz exports too)')
    c.add_argument('--force', action='store_true', help='replace a setup that already has sources (a copy of it is kept)')
    c = add('fleet-pair', 'Pair this Machine with your account for a Fleet, with a code from that account', cmd_fleet_pair)
    c.add_argument('code', help='the pairing code, like ABCD-EFGH')
    c.add_argument('--label', help='a name for this Machine there (default: this computer\'s name)')
    c.add_argument('--network', '--relay', help=argparse.SUPPRESS)  # ignored: commands copied from before the Network was built in
    add('skills', 'Skills your agent follows: yours, proposed and shared ones (edit them on the dashboard: Settings, Skills)', cmd_skills)
    c = add('skills-propose', 'Propose a skill from repeated work; it stays off until the owner approves it on the dashboard', cmd_skills_propose)
    c.add_argument('--name', required=True, help='short name, like "Answer pricing questions"')
    c.add_argument('--description', required=True, help='one line: when to use it')
    c.add_argument('--file', required=True, help='markdown file with the instructions, or - for stdin')
    c.add_argument('--why', help='one line for the owner: what repeated work this comes from')
    c = add('skills-found', 'Report from the Find my skills turn: the owner\'s account skills and the ones worth importing', cmd_skills_found)
    c.add_argument('--run', required=True, help='the skills run ID from the turn\'s message')
    c.add_argument('--file', required=True, help='the JSON report, or - for stdin')
    c = add('task-import', 'Add a task someone sent you by email, from its link (opens your dashboard to confirm)', cmd_task_import)
    c.add_argument('link', help="the task link from the email, in quotes: 'https://…/t/…#k=…'")
    c = add('service', 'The background service: status, start, stop, restart or its logs (systemd on Linux, launchd on macOS)', cmd_service)
    c.add_argument('action', choices=('status', 'start', 'stop', 'restart', 'logs'))
    c.add_argument('-n', '--lines', type=int, default=50, help='with logs: how many lines')
    c.add_argument('-f', '--follow', action='store_true', help='with logs: keep printing new lines')
    c = add('message-key', 'Where the message key is kept: status, to-keyring (the desktop keyring, Linux) or to-file (back next to the database)', cmd_message_key)
    c.add_argument('action', choices=('status', 'to-keyring', 'to-file'))
    c.add_argument('--keep-in-keyring', action='store_true', help='with to-file: leave a copy in the keyring')
    c = add('support-bundle', 'Save versions, doctor and recent logs in one file to send when asking for help; no keys or tokens', cmd_support_bundle)
    c.add_argument('--out', help='folder to save it in (default: here; on Windows WSL your Windows Desktop)')
    c = add('raw', 'Call any non-owner service action with JSON args', cmd_raw)
    c.add_argument('action')
    c.add_argument('args', nargs='?', help='JSON object')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        args.func({} if args.func in (cmd_doctor, cmd_import, cmd_service, cmd_support_bundle) else load_config(args.config), args)
    except CliError as error:
        sys.exit(f'assistant: {error}')
    except BrokenPipeError:
        pass


if __name__ == '__main__':
    main()
