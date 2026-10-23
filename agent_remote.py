"""Hand to Claude Code / Hand to Codex where the CLI isn't the owner's own computer (agent_jobs.py, docs/agents.md).

- Claude (`remote`): the brief goes as a message to the owner's own Claude Code session, `claude -p <message> --cloud
  <session id>`. That session runs on their paired computer or in Anthropic's cloud, under their own Claude sign-in.
  It can't reach this Machine, so it hands the result back through The Machine's claude.ai connector: one tool,
  `report_result`, with a ticket made here for this job only. The Network keeps only the ticket's sha256 and the
  sealed result until this Machine fetches it (`/api/fleet/agent-results`).
- Codex (`cloud`): `codex cloud exec --env <id>`, followed with `codex cloud list --json` and, once it is ready,
  `codex cloud diff`. It runs in Codex cloud, under the owner's own ChatGPT sign-in.

Neither keeps or reads a Claude or ChatGPT credential: the CLIs hold their own.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import secrets
import subprocess
import threading
import time

import agent_jobs as aj

LOG = logging.getLogger('personal-assistant')
SESSION = re.compile(r'(?:session_|cse_)[A-Za-z0-9]{8,64}')
CODEX_TASK = re.compile(r'\btask_[a-z]_[0-9a-f]{16,64}\b')
REMOTE_DAYS = 2           # a remote Claude job waits this long for its result
POLL = 20                 # seconds between fetches while a remote job waits
CODEX_POLL = 15
DONE = ('ready', 'applied')
FAILED = ('error', 'failed', 'cancelled')


def runner(row, command, own, jobs):
    if row['tool'] == 'claude':
        if not own.get('session'):
            raise aj.Stopped('missing', 'Pair a Claude Code session first')
        return RemoteClaude(command, own, jobs)
    if not own.get('env'):
        raise aj.Stopped('missing', 'Choose a Codex cloud environment first')
    return CloudCodex(command, own, jobs)


def session_id(value):
    """A Claude Code session id from what the owner pasted: the id itself or its claude.ai/code link."""
    found = SESSION.search(str(value or ''))
    return found.group(0) if found else None


def brief(prompt, ticket, mcp_url):
    return (f"{prompt.rstrip()}\n\n---\nThis job comes from The Machine. When you are done, call the `report_result` tool "
            f"of The Machine connector once, with ticket `{ticket}`, a short markdown summary, the finished text in `draft` "
            f"if the job asked for a message, and links such as a pull request. If that tool is not there, say so and "
            f"ask the owner to add the connector in claude.ai (Settings, Connectors, Add custom connector: {mcp_url}).")


def _run(argv, cwd, env, timeout=120):
    out = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    said = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', (out.stdout or '') + '\n' + (out.stderr or ''))
    return out.returncode, said


def _last_line(said, fallback):
    lines = [x.strip() for x in said.splitlines() if x.strip()]
    return (lines[-1] if lines else fallback)[:300]


class RemoteClaude:
    def __init__(self, command, own, jobs):
        self.command, self.own, self.jobs = command, own, jobs
        self.config_dir = own.get('config_dir') if isinstance(own.get('config_dir'), str) else None

    def run(self, job, prompt, resume, folder, transcript, timeout, on_progress, on_session, procs):
        session = session_id(self.own.get('session'))
        if not session:
            raise aj.Stopped('missing', 'The paired Claude Code session id is not valid')
        ticket = secrets.token_urlsafe(24)
        digest = hashlib.sha256(ticket.encode()).hexdigest()
        try:
            made = self.jobs.network({'action': 'register', 'ticket_hash': digest, 'job': job['id']}) or {}
        except Exception as error:  # noqa: BLE001 - the Network's own message, never a secret
            raise aj.Stopped('failed', f'The Network could not take the job: {error}'[:300]) from None
        with self.jobs.connect() as db:
            db.execute('UPDATE agent_jobs SET token_hash=? WHERE id=?', (digest, job['id']))
        code, said = _run([self.command, '-p', brief(prompt, ticket, made.get('mcp_url') or 'https://app.themachine.computer/mcp'),
                           '--cloud', session], folder, aj.run_env(self.config_dir))
        with open(transcript, 'a', encoding='utf-8') as log:
            log.write(said[-20000:].replace(ticket, '<ticket>'))
        if code:
            raise aj.Stopped('failed', _last_line(said.replace(ticket, '<ticket>'), f'Claude Code stopped (exit {code})'))
        on_session(session)
        watch(self.jobs)
        return session, None  # the result comes back through the connector

    def link(self, session):
        return 'https://claude.ai/code/' + session if SESSION.fullmatch(str(session or '')) else 'https://claude.ai/code'

    def resume_watch(self, row):
        watch(self.jobs)


_watching = set()


def watch(jobs):
    """One thread per AgentJobs that fetches remote results while a remote job waits."""
    with jobs.lock:
        if id(jobs) in _watching:
            return
        _watching.add(id(jobs))
    threading.Thread(target=_poll, args=(jobs,), daemon=True, name='agent-results').start()


def _waiting(jobs):
    with jobs.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM agent_jobs WHERE (place='remote' AND status IN ('waiting','running') "
                                            "AND token_hash IS NOT NULL) OR token_hash IN (SELECT ticket_hash FROM agent_result_receipts WHERE acked=0)")]


def _poll(jobs):
    try:
        while True:
            rows = _waiting(jobs)
            if not rows:
                return
            fetch(jobs, rows)
            time.sleep(POLL)
    finally:
        with jobs.lock:
            _watching.discard(id(jobs))


def fetch(jobs, rows=None):
    rows = _waiting(jobs) if rows is None else rows
    if not rows:
        return 0
    by_hash = {r['token_hash']: r for r in rows}
    try:
        found = (jobs.network({'action': 'fetch', 'tickets': list(by_hash)}) or {}).get('results') or []
    except Exception as error:  # noqa: BLE001 - try again on the next round
        LOG.warning('agent results fetch error=%s', type(error).__name__)
        found = []
    took = []
    for result in found:
        row = by_hash.get(result.get('ticket_hash'))
        if row:
            if jobs.remote_result(row['id'], result):
                took.append(row['token_hash'])
    with jobs.connect() as db:
        receipts = [dict(r) for r in db.execute('SELECT ticket_hash,report FROM agent_result_receipts WHERE acked=0')]
    if receipts:
        try:
            reply = jobs.network({'action': 'ack', 'receipts': receipts}) or {}
        except Exception as error:  # noqa: BLE001 - durable receipts are retried after a restart too
            LOG.warning('agent results ack error=%s', type(error).__name__)
        else:
            with jobs.connect() as db:
                db.executemany('UPDATE agent_result_receipts SET acked=1 WHERE ticket_hash=? AND report=?',
                               [(r['ticket_hash'], r['report']) for r in reply.get('acked') or []
                                if r in receipts])
    cutoff = time.time() - REMOTE_DAYS * 86400
    for row in rows:
        started = row['started_at'] or row['created_at']
        if row['token_hash'] not in took and dt.datetime.fromisoformat(started).timestamp() < cutoff:
            jobs.finish(row['id'], 'failed', error='No result came back. Open it in Claude to see where it got to.',
                        error_kind='timeout', only_if=('waiting', 'running'))
    return len(took)


class CloudCodex:
    def __init__(self, command, own, jobs):
        self.command, self.own, self.jobs = command, own, jobs
        self.env = str(own.get('env'))

    def run(self, job, prompt, resume, folder, transcript, timeout, on_progress, on_session, procs):
        branch = self.own.get('branch')
        argv = [self.command, 'cloud', 'exec', '--env', self.env, *(['--branch', str(branch)] if branch else []), prompt]
        code, said = _run(argv, folder, aj.run_env())
        with open(transcript, 'a', encoding='utf-8') as log:
            log.write(said[-20000:])
        task = CODEX_TASK.search(said)
        if code or not task:
            raise aj.Stopped('failed', _last_line(said, f'Codex cloud stopped (exit {code})'))
        on_session(task.group(0))
        return self.follow(job, task.group(0), time.time(), timeout, on_progress, procs)

    def task(self, task_id):
        code, said = _run([self.command, 'cloud', 'list', '--json', '--env', self.env, '--limit', '20'], None, aj.run_env(), 60)
        try:
            tasks = json.loads(said.strip().splitlines()[0] if said.strip() else '{}').get('tasks') or []
        except (ValueError, AttributeError, IndexError):
            tasks = []
        return next((t for t in tasks if isinstance(t, dict) and t.get('id') == task_id), None)

    def follow(self, job, task_id, started, timeout, on_progress, procs):
        handle = aj.Handle(lambda: None)
        procs[job['id']] = handle
        last = None
        try:
            while True:
                if handle.cancelled:
                    raise aj.Stopped('cancelled', 'Cancelled here. It may still finish in Codex cloud.')
                if time.time() - started > max(timeout, 3600):
                    raise aj.Stopped('timeout', 'Still running in Codex cloud. Open it there.')
                found = self.task(task_id) or {}
                status = str(found.get('status') or '').lower()
                if status and status != last:
                    on_progress({'pending': 'Running in Codex cloud'}.get(status, 'Codex cloud: ' + status)[:160])
                    last = status
                if status in FAILED:
                    raise aj.Stopped('failed', 'Codex cloud stopped. Open it there to see why.')
                if status in DONE:
                    code, diff = _run([self.command, 'cloud', 'diff', task_id], None, aj.run_env(), 120)
                    diff = diff.strip() + '\n' if not code and diff.strip() else ''
                    files = sorted(set(re.findall(r'^\+\+\+ b/(.+)$', diff, re.M)))[:aj.FILES_MAX]
                    title = str(found.get('title') or 'Done in Codex cloud').strip()
                    summary = f"{title}\n\n{len(files)} file{'s' if len(files) != 1 else ''} changed. Open it in Codex cloud to make a pull request."
                    self.jobs.result(job['id'], task_id, summary[:aj.SUMMARY_MAX], files=[{'path': f, 'size': 0} for f in files],
                                     diff=diff[:aj.DIFF_MAX])
                    return task_id, None  # the result is in already
                time.sleep(CODEX_POLL)
        finally:
            procs.pop(job['id'], None)

    def link(self, task_id):
        return 'https://chatgpt.com/codex/tasks/' + task_id if CODEX_TASK.fullmatch(str(task_id or '')) else 'https://chatgpt.com/codex'

    def resume_watch(self, row):
        if row['session_id'] and CODEX_TASK.fullmatch(row['session_id']):
            def go():
                try:
                    since = dt.datetime.fromisoformat(row['started_at']).timestamp() if row['started_at'] else time.time()
                    self.follow(row, row['session_id'], since, self.jobs.timeout, lambda line: self.jobs.progress(row['id'], line), self.jobs.procs)
                except aj.Stopped as stop:
                    self.jobs.finish(row['id'], 'cancelled' if stop.kind == 'cancelled' else 'failed', error=str(stop)[:500], error_kind=stop.kind)
            threading.Thread(target=go, daemon=True, name='agent-job').start()
