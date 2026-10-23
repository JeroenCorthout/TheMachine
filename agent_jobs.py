"""Hand a card's job to Claude Code or Codex (docs/agents.md).

The owner opens Hand to Claude Code (or Hand to Codex) on a card, reads and edits the brief, and sends it. The card then
shows the job: Sent, Running, a Result (the summary, the files it made or changed, the diff, links) or Failed. From a
result the owner marks the card done, asks for changes (a follow-up in the same session) or hands it off again.

Where a job runs (`place`):
- `local`: the CLI on this computer, `claude -p --output-format stream-json` or `codex exec --json`, in a folder of
  its own (`<database folder>/agent-jobs/<id>/work`) with the brief and the card's files. The owner's own CLI config,
  plugins, MCP servers and permission settings apply as they are: nothing is added (no bypass flags) and nothing is
  taken away. A follow-up resumes the same session (`claude --resume`, `codex exec resume`).
- `remote` (Claude, hosted Machines): the job is queued into the owner's own Claude session on their paired computer
  (`claude -p --cloud <session>`), with the unmodified `claude` the owner signed in through Anthropic's own flow. Its
  result comes back through The Machine's claude.ai connector (`report_result`), with a token for that job only.
- `cloud` (Codex, hosted Machines): `codex cloud exec`, polled with `codex cloud status` and `codex cloud diff`. It runs
  in Codex cloud, not on the owner's computer, and the card says so.

Credentials stay with the CLIs: this module never reads, copies or stores them. Transcripts and results stay in the job
folder, readable only by the owner's account. Config (`agents` in config.json, all optional):
    {"claude": {"command": "claude", "place": "local", "config_dir": "~/.claude-other", "session": "<session id>", "device": "Laptop"},
     "codex": {"command": "codex", "place": "local", "env": "<Codex cloud environment id>"},
     "max_parallel": 2, "timeout_minutes": 30}
`config_dir` points the jobs' Claude Code at another config folder (CLAUDE_CONFIG_DIR, another account) and only them.
`"agents": false` turns the card actions off. Without config, a CLI found on PATH (or in ~/.local/bin) shows its action.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.parse

LOG = logging.getLogger('personal-assistant')
TOOLS = {'claude': 'Claude Code', 'codex': 'Codex'}
PLACES = {'claude': ('local', 'remote'), 'codex': ('local', 'cloud')}
ACTIVE = ('sent', 'running', 'waiting')
BRIEF_MAX = 12000
ASK_MAX = 4000
SUMMARY_MAX = 20000
DIFF_MAX = 200_000
FILES_MAX = 50
TRANSCRIPT_MAX = 20 * 1024 * 1024
DRAFT_FILE = 'draft.md'
URL = re.compile(r'https?://[^\s<>()"\'`\]]+')
SCHEMA = '''CREATE TABLE IF NOT EXISTS agent_jobs(id TEXT PRIMARY KEY,suggestion_id TEXT NOT NULL,tool TEXT NOT NULL,place TEXT NOT NULL,
  status TEXT NOT NULL,brief TEXT NOT NULL,asks TEXT,session_id TEXT,url TEXT,progress TEXT,summary TEXT,diff TEXT,files TEXT,
  error TEXT,error_kind TEXT,turns INTEGER NOT NULL DEFAULT 1,outcome TEXT,token_hash TEXT,created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,draft_updated INTEGER,answers TEXT);
CREATE INDEX IF NOT EXISTS agent_jobs_card ON agent_jobs(suggestion_id,created_at);'''


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def settings(config):
    value = config.get('agents')
    return value if isinstance(value, dict) else ({} if value is None else None)


def find(name, command=None):
    """The CLI's path: as configured, else on PATH, else in ~/.local/bin (where the official installers put it)."""
    if command:
        path = Path(os.path.expanduser(str(command)))
        return str(path) if path.is_absolute() and os.access(path, os.X_OK) else shutil.which(str(command))
    return shutil.which(name) or shutil.which(name, path=str(Path.home() / '.local/bin'))


def tools(config):
    """The tools this Machine can hand jobs to, with where they run: [{id, name, place, device?}]. A hosted Machine
    (one with a manage_url) has no local CLI of the owner's: there Claude needs its paired session and Codex its cloud
    environment set up first."""
    found = settings(config)
    if found is None:
        return []
    hosted = bool(config.get('manage_url'))
    out = []
    for tool, name in TOOLS.items():
        own = found.get(tool)
        if own is False:
            continue
        own = own if isinstance(own, dict) else {}
        place = own.get('place') or ('remote' if tool == 'claude' else 'cloud') if hosted else own.get('place') or 'local'
        if place not in PLACES[tool] or not find(tool, own.get('command')):
            continue
        if place == 'remote' and not own.get('session') or place == 'cloud' and not own.get('env'):
            continue
        entry = {'id': tool, 'name': name, 'place': place}
        if place == 'remote':
            entry['device'] = str(own.get('device') or 'your computer')[:60]
        out.append(entry)
    return out


def plain(text):
    return re.sub(r'\[([^\]\n]*)\]\((https?://[^)\s]+)\)', r'\1 (\2)', str(text or ''))


def gist(text, chars):
    """One line of at most chars, ending at a whole sentence (or word) instead of mid-word."""
    text = ' '.join(plain(text).split())
    if len(text) <= chars:
        return text
    cut = text[:chars]
    end = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
    return cut[:end + 1] if end > chars // 3 else cut[:cut.rfind(' ')].rstrip(',;:') + '…'


INLINE_MAX = 7000  # source text written into a job that runs away from this Machine (remote Claude Code, Codex cloud)


def inline_messages(sources, budget):
    """The source messages written into the job itself, newest first, within budget characters: for a job that runs
    where the job folder isn't (remote Claude Code, Codex cloud). Each gets its share (the newest up to half); what
    doesn't fit is cut with a note saying so, and older ones that don't fit at all are counted as left out."""
    items = sorted(sources, key=lambda v: str(v.get('occurred_at') or ''), reverse=True)
    out, left = [], budget
    share = max(budget // max(len(items), 1), 1500)  # each message gets its share; the newest may take up to half
    for n, v in enumerate(items):
        head = '\n'.join([f"### {v.get('title') or 'Message'}", f"From: {v.get('from') or 'unknown'}",
                          *[f'{k}: {v[k.lower()]}' for k in ('To', 'Cc') if v.get(k.lower())], f"When: {v.get('occurred_at') or ''}", '', ''])
        body = str(v.get('body') or '').strip() or '(no text)'
        room = min(left, max(share, budget // 2) if n == 0 else share) - len(head) - 120
        if room < 200:
            out.append(f'({len(items) - n} older message{"s" if len(items) - n > 1 else ""} left out here, to keep this short.)')
            break
        if len(body) > room:
            cut = body[:room].rsplit(' ', 1)[0]
            body = cut + f'\n(Cut here: {len(body) - len(cut)} more characters in the original.)'
        if v.get('clipped'):
            body += '\n(The stored copy was already cut short; the rest is in the original.)'
        out.append(head + body)
        left -= len(out[-1]) + 2
    return '\n\n'.join(out)


def brief_for(card, drafts=(), links=(), files=(), messages=(), task=None, note=None, about=(), history=(), sources=(), inline=False):
    """The job as the owner first sees it, in the order the agent needs it: the task (the card's next step, never its
    latest chat reply), the owner's own note word for word, who and what it is about, what happened so far (as
    background), what the card prepared, the source messages in the folder (links only as a second reference), what
    done looks like and the limits. They edit it before it goes. `inline`: the job runs away from this Machine, where
    the folder isn't, so the source messages (`sources`) go into the text itself, newest first, under a cap."""
    title = ' '.join(str(card.get('title') or '').split())
    task = ' '.join(str(task or '').split()) or (title.rstrip('.') + '.' if title else 'Do the next step on this card.')
    lines = ['Task: ' + task, '']
    note = str(note or '').strip()
    if note:
        lines += ['My note on the card, as I wrote it:', note[:2000], '']
    lines += ['About:', f'- The card in The Machine: {title or "a task"}', *[f'- {a}' for a in list(about)[:6]], '']
    history = [h for h in history if str(h).strip()][-6:]
    body = plain(card.get('body')).strip()
    if history or body:
        lines.append('So far (background only; the task above is what to do):')
        lines += [f'- {h}' for h in history] if history else [f'- The card says: {gist(body, 800)}']
        lines.append('')
    if drafts:
        lines.append('Prepared on the card:')
        for d in list(drafts)[:5]:
            to = d.get('to_name') or d.get('to_email') or ', '.join(d.get('to') or [])
            head = (d.get('label') or d.get('kind') or 'Draft') + (f' (to {to})' if to else '')
            lines.append(f'- {head}: ' + plain(d.get('body') or d.get('message') or '')[:1500].replace('\n', '\n  '))
        lines.append('')
    if messages and not (inline and sources):
        lines += ['The source messages, with their full text, are in card/messages.md in your folder. Read them first: '
                  'you don\'t need the mailbox or the links for them.', '']
    at = len(lines)  # where the source messages go when they travel in the text itself
    names = [f'card/{Path(f).name}' for f in files]
    if names:
        lines += ['Other files from the card, in your folder: ' + ', '.join(names), '']
    if links:
        lines += ['Links, only if you need more than what is here:', *[f'- {u}' for u in list(dict.fromkeys(links))[:10]], '']
    lines += ['Done looks like: the task above done, or taken as far as it can go from here with what is left for me said plainly. '
              'Then a short summary of what you did and found, with any files you made in this folder. Start with the answer '
              'or the finding itself; don\'t list the files you saved or say what you didn\'t send, as I see your files myself.', '',
              'Limits:', '- You may read what you need and make or change files in this folder.']
    if drafts:
        lines.append(f'- If you rewrite the message prepared above, save only its new text (no To or Subject lines) in {DRAFT_FILE}: '
                     'it replaces the draft on the card, which I check and send myself.')
    lines.append('- Leave anything that sends, publishes, pays or submits for me: prepare it and say what is left.')
    if inline and sources:
        # Within what the brief may hold, so the limits at its end are never cut off.
        budget = min(INLINE_MAX, BRIEF_MAX - len('\n'.join(lines)) - 300)
        text = inline_messages(sources, budget) if budget > 400 else ''
        lines[at:at] = (['The source messages, newest first, with their text (read them first; you don\'t need the mailbox '
                         'or the links for them):', '', text, ''] if text else
                        ['The source messages didn\'t fit here; the links below open them.', ''])
    return '\n'.join(lines).strip()[:BRIEF_MAX]


def gets_for(parts):
    """What a job gets from the card, as small chips for the hand-off sheet: counts and file names, never links."""
    out, kinds = [], [str(k) for k in parts.get('message_kinds') or ()]
    count = len(kinds) or (str(parts.get('messages') or '').count('\n## ') + 1 if parts.get('messages') else 0)
    if count:
        mail = kinds and all(k in ('email', 'gmail', 'outlook') or k.startswith(('gmail.', 'outlook.')) for k in kinds)
        out.append({'kind': 'mail' if mail else 'chat', 'label': f"{count} {'email' if mail else 'message'}{'' if count == 1 else 's'}"})
    for d in list(parts.get('drafts') or ())[:3]:
        to = str(d.get('to_name') or '').split()
        out.append({'kind': 'draft', 'label': 'Draft to ' + to[0] if to else 'The draft'})
    out += [{'kind': 'file', 'label': Path(f).name} for f in list(parts.get('files') or ())[:5]]
    if parts.get('messages'):
        out.append({'kind': 'file', 'label': 'messages.md'})
    links = len(dict.fromkeys(parts.get('links') or ()))
    if links:
        out.append({'kind': 'link', 'label': f"{links} link{'' if links == 1 else 's'}"})
    return out


def with_note(brief, note):
    """The owner's own note for the job goes first, so it reads before the card's context."""
    note = str(note or '').strip()
    return f'A note from me: {note}\n\n{brief}' if note else brief


def kill(proc):
    """Stop a job's process and everything it started (its own process group)."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


# Why a job stopped, in plain words, and what Try again does about it.
RESTARTED = 'The Machine restarted (an update, or the computer) while it worked on this, which stopped it. Try again carries on where it left off.'
# What Try again tells a session that stopped during the handoff itself.
CARRY_ON = ('You were stopped before you finished (The Machine restarted or the run was cut off). Carry on with the task '
            'from where you left off, then end with the summary it asks for.')


class Stopped(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def git_dir(work):
    """The job's own git history, next to its folder rather than in it: a folder that is a git repository of its own
    is a new project to Claude Code, so it would not inherit the trust the owner gave the agent-jobs folder once.
    Jobs from before keep their work/.git."""
    work = Path(work)
    return work / '.git' if (work / '.git').exists() else work.parent / 'git'


def git(folder, *args, check=True):
    """Git in a job folder only, never the owner's own git config, hooks or signing."""
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',
           'GIT_DIR': str(git_dir(folder)), 'GIT_WORK_TREE': str(folder)}
    return subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'user.name=The Machine', '-c', 'user.email=machine@localhost',
                           '-c', 'commit.gpgsign=false', '-c', 'core.quotepath=false', *args], cwd=folder, env=env, capture_output=True,
                          text=True, timeout=60, check=check)


# ------------------------------------------------------------------ runners: one turn of a job

def job_name(job):
    """What the job is called in the owner's Claude and Codex apps: the card's title, else the brief's first line."""
    title = re.sub(r'\s+', ' ', str(job.get('card_title') or '')).strip()
    return (title or (str(job.get('brief') or '').strip().splitlines() or ['A job from The Machine'])[0])[:80]


def claude_dir(config_dir=None):
    """Claude Code's config folder: the jobs' own `config_dir`, else CLAUDE_CONFIG_DIR, else ~/.claude."""
    found = config_dir or os.environ.get('CLAUDE_CONFIG_DIR')
    return Path(os.path.expanduser(str(found))) if found else Path.home() / '.claude'


def run_env(config_dir=None):
    return {**os.environ, 'NO_COLOR': '1', **({'CLAUDE_CONFIG_DIR': str(claude_dir(config_dir))} if config_dir else {})}


class Local:
    """A turn with the CLI on this computer. Yields progress lines; returns (session_id, summary)."""
    def __init__(self, command, config_dir=None):
        self.command = command
        self.config_dir = config_dir

    def argv(self, job, resume):
        raise NotImplementedError

    def run(self, job, prompt, resume, folder, transcript, timeout, on_progress, on_session, procs):
        argv = self.argv(job, resume)
        with open(transcript, 'ab') as log, open(str(transcript) + '.err', 'ab') as err:
            proc = subprocess.Popen(argv, cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                    start_new_session=True, env=run_env(self.config_dir))
            procs[job['id']] = proc
            timer = threading.Timer(timeout, lambda: (setattr(proc, 'timed_out', True), kill(proc)))
            timer.daemon = True
            timer.start()
            state = {'session': resume, 'summary': '', 'error': '', 'size': 0}
            try:
                try:
                    proc.stdin.write(prompt.encode())
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                for raw in proc.stdout:
                    if state['size'] < TRANSCRIPT_MAX:
                        log.write(raw)
                        state['size'] += len(raw)
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        line = self.event(event, state)
                        if state['session'] and state['session'] != resume:
                            on_session(state['session'])
                            resume = state['session']
                        if line:
                            on_progress(line)
                proc.wait()
            finally:
                timer.cancel()
                procs.pop(job['id'], None)
                proc.stdout.close()
        if getattr(proc, 'cancelled', False):
            raise Stopped('cancelled', 'Cancelled')
        if getattr(proc, 'timed_out', False):
            raise Stopped('timeout', f'It ran longer than its {round(timeout / 60)}-minute limit, so it was stopped')
        if state['error'] or proc.returncode:
            raise Stopped('failed', state['error'] or self.stderr_hint(transcript) or f'{TOOLS[job["tool"]]} stopped (exit {proc.returncode})')
        return state['session'], state['summary']

    @staticmethod
    def stderr_hint(transcript):
        try:
            text = Path(str(transcript) + '.err').read_text(errors='replace')[-4000:]
        except OSError:
            return ''
        lines = [x.strip() for x in text.splitlines() if x.strip() and not x.startswith(('at ', '    '))]
        return lines[-1][:300] if lines else ''


class LocalClaude(Local):
    def argv(self, job, resume):
        # The owner's own settings decide permissions, plugins and MCP servers: no --permission-mode, no --bare.
        return [self.command, '-p', '--output-format', 'stream-json', '--verbose', *(['--resume', resume] if resume else [])]

    @staticmethod
    def event(event, state):
        kind = event.get('type')
        if event.get('session_id') and not state['session']:
            state['session'] = str(event['session_id'])[:120]
        if kind == 'assistant':
            parts = (event.get('message') or {}).get('content') or []
            for part in parts if isinstance(parts, list) else []:
                if part.get('type') == 'tool_use':
                    target = (part.get('input') or {})
                    detail = target.get('file_path') or target.get('description') or target.get('pattern') or target.get('url') or ''
                    return (f"{part.get('name')}: {Path(str(detail)).name if target.get('file_path') else detail}" if detail else str(part.get('name')))[:160]
                if part.get('type') == 'text' and str(part.get('text') or '').strip():
                    return str(part['text']).strip().splitlines()[0][:160]
        if kind == 'result':
            state['session'] = str(event.get('session_id') or state['session'] or '')[:120] or None
            if event.get('is_error') or event.get('subtype') not in (None, 'success'):
                state['error'] = str(event.get('result') or event.get('subtype') or 'Claude Code stopped')[:500]
            else:
                state['summary'] = str(event.get('result') or '')
        return None


class LocalCodex(Local):
    def __init__(self, command, git_ok=True):
        super().__init__(command)
        self.git_ok = git_ok

    def argv(self, job, resume):
        # The owner's own ~/.codex config decides model, sandbox and approvals. Only without git (no repository for
        # the job folder) does it skip Codex's git check, which is about the folder, not about permissions.
        check = [] if self.git_ok else ['--skip-git-repo-check']
        if resume:
            return [self.command, 'exec', 'resume', *check, '--json', resume, '-']
        return [self.command, 'exec', *check, '--json', '-']

    @staticmethod
    def event(event, state):
        kind = event.get('type')
        if kind == 'thread.started' and event.get('thread_id'):
            state['session'] = str(event['thread_id'])[:120]
        item = event.get('item') if isinstance(event.get('item'), dict) else {}
        if kind == 'item.completed' and item.get('type') == 'agent_message':
            text = str(item.get('text') or '').strip()
            if text:
                state['summary'] = text
                return text.splitlines()[0][:160]
        if kind == 'item.started' and item.get('type') == 'command_execution':
            return ('Running ' + str(item.get('command') or ''))[:160]
        if kind == 'item.completed' and item.get('type') == 'file_change':
            names = [Path(str(c.get('path') or '')).name for c in item.get('changes') or [] if isinstance(c, dict)]
            return ('Changed ' + ', '.join(n for n in names if n))[:160] if names else None
        if kind in ('turn.failed', 'error'):
            error = event.get('error') if isinstance(event.get('error'), dict) else {}
            state['error'] = str(error.get('message') or event.get('message') or 'Codex stopped')[:500]
        return None


# ------------------------------------------------------------------ runners the owner also sees in their own apps

class Untrusted(Exception):
    """`claude --bg` won't start in a folder the owner hasn't trusted: the job runs with `claude -p` instead."""


class Handle:
    """What cancel and the timeout stop for a job that isn't one child process: `stop()` ends it where it runs."""
    def __init__(self, stop):
        self.stop, self.cancelled, self.timed_out = stop, False, False


def stop_proc(proc):
    """Cancel or time out a job: its own way (Handle.stop) or its process group."""
    if hasattr(proc, 'stop'):
        try:
            proc.stop()
        except Exception as error:  # noqa: BLE001
            LOG.warning('agent job stop error=%s', type(error).__name__)
    else:
        kill(proc)


def claude_transcript(session_id, config_dir=None):
    """Claude Code's own transcript of a session (~/.claude/projects/<folder>/<session id>.jsonl, see its docs on
    sessions): where the background session's last words are read from."""
    if not re.fullmatch(r'[0-9a-f-]{36}', str(session_id or '')):
        return None
    found = sorted((claude_dir(config_dir) / 'projects').glob(f'*/{session_id}.jsonl'))
    return found[0] if found else None


def last_words(transcript, since):
    """The last thing Claude said in a transcript, written at or after `since` (epoch seconds): the turn's summary."""
    text = None
    try:
        with open(transcript, encoding='utf-8', errors='replace') as lines:
            for line in lines:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get('type') != 'assistant':
                    continue
                try:
                    at = dt.datetime.fromisoformat(str(entry.get('timestamp')).replace('Z', '+00:00')).timestamp()
                except ValueError:
                    continue
                if at < since - 2:
                    continue
                parts = (entry.get('message') or {}).get('content') or []
                said = '\n'.join(str(p.get('text') or '') for p in parts if isinstance(p, dict) and p.get('type') == 'text').strip()
                if said:
                    text = said
    except OSError:
        return None
    return text


RC_ON = json.dumps({'remoteControlAtStartup': True})


class BackgroundClaude:
    """A turn as a Claude Code background session (`claude --bg --name <card>`, see Claude Code's agent view docs):
    the owner sees it in `claude agents` and, with Remote Control on for all sessions, at claude.ai/code and in the
    Claude apps, where they can watch it and carry on. The Machine follows it with `claude agents --json --all`, the
    supported way to read session state, and reads the result from the session's transcript."""
    POLL = 3

    def __init__(self, command, config_dir=None):
        self.command = command
        self.config_dir = config_dir

    def agents(self):
        out = subprocess.run([self.command, 'agents', '--json', '--all'], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL, env=run_env(self.config_dir))
        try:
            found = json.loads(out.stdout or '[]')
        except ValueError:
            return []
        return [e for e in (found if isinstance(found, list) else found.get('sessions') or []) if isinstance(e, dict)]

    def entry(self, short=None, session=None):
        for e in self.agents():
            if e.get('kind') == 'background' and (short and e.get('id') == short or session and e.get('sessionId') == session):
                return e
        return None

    def run(self, job, prompt, resume, folder, transcript, timeout, on_progress, on_session, procs):
        name = job_name(job)
        # Remote Control on for this session (the owner's `disableRemoteControl` still wins): it is what puts the session
        # in claude.ai/code and the Claude apps, where the owner reads it and keeps talking to it.
        argv = [self.command, *(['--resume', resume] if resume else []), '--bg', '--settings', RC_ON, *([] if resume else ['--name', name]), '--', prompt]
        started = time.time()
        out = subprocess.run(argv, cwd=folder, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                             env=run_env(self.config_dir))
        said = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', (out.stdout or '') + '\n' + (out.stderr or ''))
        with open(transcript, 'a', encoding='utf-8') as log:
            log.write(said[-20000:])
        if 'not trusted' in said.lower():
            raise Untrusted()
        found = re.search(r'backgrounded\s*·\s*([0-9a-f]{6,})', said)
        if out.returncode or not found:
            lines = [x.strip() for x in said.splitlines() if x.strip()]
            raise Stopped('failed', (lines[-1] if lines else f'Claude Code stopped (exit {out.returncode})')[:300])
        return self.watch(job, found.group(1), started, timeout, on_progress, on_session, procs)

    def watch(self, job, short, started, timeout, on_progress, on_session, procs):
        handle = Handle(lambda: subprocess.run([self.command, 'stop', short], capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                                                     env=run_env(self.config_dir)))
        procs[job['id']] = handle
        session, seen_working, last = None, False, None
        try:
            while True:
                if handle.cancelled:
                    raise Stopped('cancelled', 'Cancelled')
                if time.time() - started > timeout:
                    handle.timed_out = True
                    stop_proc(handle)
                    raise Stopped('timeout', f'It ran longer than its {round(timeout / 60)}-minute limit, so it was stopped')
                e = self.entry(short=short)
                if e is None:
                    raise Stopped('failed', 'The Claude Code session is gone')
                if e.get('sessionId') and e['sessionId'] != session:
                    session = str(e['sessionId'])[:120]
                    on_session(session)
                state, detail = e.get('state'), str(e.get('detail') or '').strip()
                if state == 'working':
                    seen_working = True
                    line = detail
                elif state == 'blocked':
                    line = 'Waiting for you in Claude' + (': ' + detail if detail else '')
                else:
                    line = None
                if line and line != last:
                    on_progress(line[:160])
                    last = line
                if state in ('failed', 'stopped'):
                    raise Stopped('cancelled' if handle.cancelled else 'failed', detail or f'Claude Code {state}')
                if state == 'done':
                    path = claude_transcript(session, self.config_dir)
                    words = last_words(path, started) if path else None
                    if words is not None or seen_working and time.time() - started > 20:
                        return session, words or detail or 'Done.'
                time.sleep(self.POLL)
        finally:
            procs.pop(job['id'], None)

    def link(self, session):
        """Its page at claude.ai/code: read from Claude Code's own job state (not a stable file, so only for the
        link; without it the session is still in the list there under the card's name)."""
        e = self.entry(session=session)
        bridge = None
        if e and e.get('id'):
            try:
                bridge = json.loads((claude_dir(self.config_dir) / 'jobs' / str(e['id']) / 'state.json').read_text()).get('bridgeSessionId')
            except (OSError, ValueError, AttributeError):
                bridge = None
        return 'https://claude.ai/code/' + bridge if bridge and re.fullmatch(r'cse_[A-Za-z0-9]{8,64}', str(bridge)) else 'https://claude.ai/code'


class NoAppServer(Exception):
    """This Codex has no app server to talk to: the job runs with `codex exec` instead."""


# Where the Codex desktop app keeps the codex it runs (Linux packages, macOS app bundles).
DESKTOP_CODEX = ('/usr/lib/chatgpt/resources/codex', '/usr/lib/codex/resources/codex', '/opt/Codex/resources/codex',
                 '/Applications/Codex.app/Contents/Resources/codex', '/Applications/ChatGPT.app/Contents/Resources/codex')


def desktop_codex():
    return next((p for p in DESKTOP_CODEX if os.path.isfile(p) and os.access(p, os.X_OK)), None)


class StdioLines:
    """The app server's stdio (`--listen stdio://`): one JSON-RPC message per line."""
    def __init__(self, proc):
        self.proc = proc

    def send(self, message):
        self.proc.stdin.write((json.dumps(message) + '\n').encode())
        self.proc.stdin.flush()

    def readline(self):
        return self.proc.stdout.readline()


class AppSocket:
    """The app server's WebSocket on loopback (`--listen ws://127.0.0.1:<port>`, `--ws-auth capability-token`): just
    enough of a client for its JSON-RPC text messages. `readline()` gives one message and b'' once it is closed."""
    def __init__(self, port, token, timeout=10):
        self.sock = socket.create_connection(('127.0.0.1', int(port)), timeout)
        self.buf, self.lock = b'', threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET / HTTP/1.1\r\nHost: 127.0.0.1:{int(port)}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                           f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nAuthorization: Bearer {token}\r\n\r\n').encode())
        while b'\r\n\r\n' not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk or len(self.buf) > 65536:
                self.close()
                raise OSError('the app server did not answer')
            self.buf += chunk
        head, self.buf = self.buf.split(b'\r\n\r\n', 1)
        if b' 101 ' not in head.split(b'\r\n', 1)[0] + b' ':
            self.close()
            raise OSError('the app server refused the connection')
        self.sock.settimeout(None)

    def take(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def frame(self, op, data):
        mask, n = os.urandom(4), len(data)
        size = bytes([0x80 | n]) if n < 126 else bytes([0xFE]) + struct.pack('>H', n) if n < 65536 else bytes([0xFF]) + struct.pack('>Q', n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.lock:
            self.sock.sendall(bytes([0x80 | op]) + size + mask + masked)

    def send(self, message):
        self.frame(1, json.dumps(message).encode())

    def readline(self):
        parts = []
        try:
            while True:
                first, second = self.take(2)
                n = second & 0x7F
                if n == 126:
                    n = struct.unpack('>H', self.take(2))[0]
                elif n == 127:
                    n = struct.unpack('>Q', self.take(8))[0]
                if n > TRANSCRIPT_MAX:
                    return b''
                mask = self.take(4) if second & 0x80 else None
                data = self.take(n)
                if mask:
                    data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
                op = first & 0x0F
                if op == 8:
                    return b''
                if op == 9:
                    self.frame(10, data)
                    continue
                if op == 10:
                    continue
                parts.append(data)
                if first & 0x80:
                    return b''.join(parts) + b'\n'
        except (EOFError, OSError, struct.error, ValueError):
            return b''

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# The service's own hardening that its jobs keep when they run in a unit of their own (systemctl show names).
HARDENING = ('NoNewPrivileges', 'PrivateTmp', 'UMask', 'ProtectSystem', 'ProtectHome', 'PrivateDevices', 'RestrictSUIDSGID',
             'LockPersonality', 'RestrictRealtime', 'ProtectKernelTunables', 'ProtectControlGroups')
# What systemd sets for each unit by itself: not carried over into the job's unit.
UNIT_ENV = re.compile(r'(INVOCATION_ID|JOURNAL_STREAM|SYSTEMD_EXEC_PID|MANAGERPID|MANAGERPIDFDID|NOTIFY_SOCKET|WATCHDOG_\w+|LISTEN_\w+|'
                      r'MEMORY_PRESSURE_\w+|MAINPID|SERVICE_RESULT|EXIT_CODE|EXIT_STATUS|MONITOR_\w+|TRIGGER_\w+)')
LISTENING = re.compile(rb'listening on: ws://127\.0\.0\.1:(\d{2,5})\b')
SERVER_FILE = 'app-server.json'


def own_unit():
    """The systemd user service The Machine runs as (personal-assistant.service), or None when it runs otherwise."""
    try:
        lines = Path('/proc/self/cgroup').read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        path = line.split(':', 2)[-1]
        name = path.rstrip('/').rsplit('/', 1)[-1]
        if '/user@' in path and re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', name):
            return name
    return None


def can_detach():
    """Whether a job's app server can run in a systemd user unit of its own (Linux, with The Machine itself a user service)."""
    return sys.platform.startswith('linux') and bool(shutil.which('systemd-run') and shutil.which('systemctl') and own_unit())


def systemctl(*args, timeout=10):
    return subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True, timeout=timeout)


def hardening(unit):
    """The service's own hardening (NoNewPrivileges, PrivateTmp, UMask, ...) as systemd-run properties."""
    try:
        out = systemctl('show', unit, *[f'--property={name}' for name in HARDENING]).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = {}
    for line in out.splitlines():
        name, _, value = line.partition('=')
        if name in HARDENING and re.fullmatch(r'[A-Za-z0-9:-]{1,20}', value):
            found[name] = value
    return found if found else None


def unit_active(unit):
    try:
        return systemctl('is-active', unit).stdout.strip() in ('active', 'activating', 'reloading')
    except (OSError, subprocess.SubprocessError):
        return False


def stop_unit(unit):
    try:
        systemctl('stop', '--no-block', unit)
    except (OSError, subprocess.SubprocessError) as error:
        LOG.warning('agent job unit stop error=%s', type(error).__name__)


def read_server(folder):
    """A job's detached app server, as start_detached wrote it in the job folder: {unit, port, token}, or None."""
    try:
        found = json.loads((Path(folder) / SERVER_FILE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict) or not re.fullmatch(r'the-machine-job-[A-Za-z0-9_-]{1,80}\.service', str(found.get('unit') or '')) \
            or not isinstance(found.get('port'), int) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', str(found.get('token') or '')):
        return None
    return found


def start_detached(server, job, work, folder, err, timeout):
    """The app server in a systemd user unit of its own (systemd-run --user), outside The Machine's service, so a
    restart of The Machine (an update) doesn't stop the job: recover() connects to it again. The unit gets the
    service's own hardening and environment (names on the command line, values from systemd-run's own environment),
    so the job runs with the same permissions as before. It listens on loopback only, behind a capability token that
    only the job folder holds. None when it can't start that way: the job then runs as a child of the service."""
    unit_of_service = own_unit()
    props = hardening(unit_of_service) if unit_of_service else None
    if props is None:
        return None
    token = secrets.token_urlsafe(32)
    unit = f'the-machine-job-{job["id"]}-{int(job.get("turns") or 1)}-{secrets.token_hex(3)}.service'
    out_path = Path(folder) / 'app-server.out'
    out_path.write_bytes(b'')
    os.chmod(out_path, 0o600)
    env = {**os.environ, 'NO_COLOR': '1'}
    argv = ['systemd-run', '--user', '--quiet', '--collect', f'--unit={unit}', f'--working-directory={work}',
            f'--property=RuntimeMaxSec={int(timeout) + 120}', '--property=KillMode=control-group',
            f'--property=StandardOutput=append:{out_path}', f'--property=StandardError=append:{err}',
            *[f'--property={k}={v}' for k, v in sorted(props.items())],
            *[f'--setenv={k}' for k in sorted(env) if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) and not UNIT_ENV.fullmatch(k)],
            '--', server, 'app-server', '--listen', 'ws://127.0.0.1:0', '--ws-auth', 'capability-token',
            '--ws-token-sha256', hashlib.sha256(token.encode()).hexdigest()]
    try:
        started = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        LOG.info('agent job detach error=%s', type(error).__name__)
        return None
    if started.returncode != 0:
        LOG.info('agent job detach exit=%s', started.returncode)
        return None
    end = time.time() + 30
    while time.time() < end:
        port = None
        for path in (err, out_path):  # its banner goes to stderr
            try:
                port = port or LISTENING.search(Path(path).read_bytes()[-65536:])
            except OSError:
                pass
        if port:
            live = {'unit': unit, 'port': int(port.group(1)), 'token': token}
            path = Path(folder) / SERVER_FILE
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(live, f)
            return live
        if not unit_active(unit):
            break
        time.sleep(0.2)
    stop_unit(unit)
    return None


class AppServerCodex:
    """A turn through `codex app-server` (Codex's documented app-server protocol): the thread is an ordinary Codex
    thread named after the card, so it shows up in the Codex app next to the owner's own threads (and, with the app's
    remote connections on, in the Codex app on their other devices), opens with codex://threads/<id> and can be
    continued there. `codex exec` threads are hidden from that list.

    It runs the app-server that ships with the Codex app when the app is installed (`server`): the app opens threads
    written by its own version, while a newer or older CLI's threads can show as an endless spinner there.

    On Linux, with The Machine itself a systemd user service, the app server runs in a unit of its own
    (start_detached) and the turn talks to it over its loopback WebSocket, so an update that restarts The Machine
    doesn't stop the job; elsewhere it is a child process on stdio."""
    def __init__(self, command, server=None, detach=False):
        self.command = command  # the CLI, for the `codex exec` fallback
        self.server = server or command
        self.detach = detach

    def run(self, job, prompt, resume, folder, transcript, timeout, on_progress, on_session, procs):
        live = None
        if self.detach and job.get('id'):
            live = start_detached(self.server, job, folder, Path(transcript).parent, str(transcript) + '.err', timeout)
        if live:
            return self.attached(job, live, transcript, timeout, procs,
                                 lambda chan, flags, log, state: self.talk(chan, flags, log, state, job, prompt, resume, folder, on_progress,
                                                                           on_session, timeout))
        with open(transcript, 'ab') as log, open(str(transcript) + '.err', 'ab') as err:
            proc = subprocess.Popen([self.server, 'app-server', '--listen', 'stdio://'], cwd=folder, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=err, start_new_session=True, env={**os.environ, 'NO_COLOR': '1'})
            procs[job['id']] = proc
            timer = threading.Timer(timeout, lambda: (setattr(proc, 'timed_out', True), kill(proc)))
            timer.daemon = True
            timer.start()
            state = {'size': 0, 'n': 0, 'summary': '', 'error': '', 'status': None}
            try:
                return self.talk(StdioLines(proc), proc, log, state, job, prompt, resume, folder, on_progress, on_session, timeout)
            finally:
                timer.cancel()
                procs.pop(job['id'], None)
                kill(proc)
                for pipe in (proc.stdin, proc.stdout):
                    try:
                        pipe.close()
                    except OSError:
                        pass

    def attached(self, job, live, transcript, timeout, procs, work):
        """`work(chan, flags, log, state)` with the job's detached app server. When it is done (or cancelled, or timed
        out) the unit stops; when The Machine itself stops first, the unit runs on and recover() rejoins it."""
        folder = Path(transcript).parent
        try:
            chan = AppSocket(live['port'], live['token'])
        except OSError:
            stop_unit(live['unit'])
            (folder / SERVER_FILE).unlink(missing_ok=True)
            raise Stopped('failed', 'I could not reach the Codex app server I started') from None
        flags = Handle(lambda: (stop_unit(live['unit']), chan.close()))
        procs[job['id']] = flags
        timer = threading.Timer(timeout, lambda: (setattr(flags, 'timed_out', True), stop_proc(flags)))
        timer.daemon = True
        timer.start()
        state = {'size': 0, 'n': 0, 'summary': '', 'error': '', 'status': None}
        try:
            with open(transcript, 'ab') as log:
                return work(chan, flags, log, state)
        finally:
            timer.cancel()
            procs.pop(job['id'], None)
            chan.close()
            stop_unit(live['unit'])
            (folder / SERVER_FILE).unlink(missing_ok=True)

    def rejoin(self, job, live, transcript, timeout, on_progress, procs):
        """After The Machine restarted: the job's turn, from its app server that ran on. It follows a turn still going
        and takes the answer of one that ended meanwhile."""
        return self.attached(job, live, transcript, timeout, procs,
                             lambda chan, flags, log, state: self.follow(chan, flags, log, state, job, on_progress, timeout))

    @staticmethod
    def rpc(chan, flags, log, state, on_progress, timeout, name='Codex'):
        def read():
            raw = chan.readline()
            if not raw:
                return None
            if state['size'] < TRANSCRIPT_MAX:
                log.write(raw)
                state['size'] += len(raw)
            try:
                found = json.loads(raw)
            except ValueError:
                return {}
            return found if isinstance(found, dict) else {}

        def call(method, params):
            state['n'] += 1
            want = state['n']
            chan.send({'jsonrpc': '2.0', 'id': want, 'method': method, 'params': params})
            while True:
                m = read()
                if m is None:
                    raise EOFError
                if m.get('id') == want and 'method' not in m:
                    if m.get('error'):
                        raise ValueError(str((m['error'] or {}).get('message') or 'error')[:300])
                    return m.get('result') or {}
                AppServerCodex.handle(m, chan.send, state, on_progress)

        def stopped():
            if getattr(flags, 'cancelled', False):
                return Stopped('cancelled', 'Cancelled')
            if getattr(flags, 'timed_out', False):
                return Stopped('timeout', f'It ran longer than its {round(timeout / 60)}-minute limit, so it was stopped')
            return None

        def until_done():
            while state['status'] is None:
                m = read()
                if m is None:
                    raise EOFError
                AppServerCodex.handle(m, chan.send, state, on_progress)
        return read, call, stopped, until_done

    @staticmethod
    def outcome(state, stopped, thread_id):
        if stopped():
            raise stopped()
        if state['status'] != 'completed':
            raise Stopped('cancelled' if state['status'] == 'interrupted' else 'failed',
                          state['error'] or ('It was interrupted' if state['status'] == 'interrupted' else 'Codex ended the turn without finishing it'))
        return thread_id, state['summary']

    def talk(self, chan, flags, log, state, job, prompt, resume, folder, on_progress, on_session, timeout):
        read, call, stopped, until_done = self.rpc(chan, flags, log, state, on_progress, timeout)
        try:
            call('initialize', {'clientInfo': {'name': 'the_machine', 'title': 'The Machine', 'version': '1'}})
        except (EOFError, ValueError, OSError):
            raise stopped() or NoAppServer() from None
        try:
            chan.send({'jsonrpc': '2.0', 'method': 'initialized'})
            # The owner's own ~/.codex config decides model, sandbox and approvals: nothing is passed to change them.
            forked = False
            if resume:
                try:
                    thread = call('thread/resume', {'threadId': resume})
                except ValueError as error:
                    if re.search(r'no rollout found|not found|no such thread|unknown thread', str(error), re.I):
                        raise Stopped('gone', 'Its Codex thread is gone, so it can’t carry on there. Hand it off again to start over.') from None
                    if 'active writer' not in str(error):
                        raise
                    # The thread is open in the Codex app, which holds it while it is: carry on in a copy of it
                    # (thread/fork copies its history into a new thread, which shows in the app too).
                    try:
                        thread, forked = call('thread/fork', {'threadId': resume}), True
                    except ValueError:
                        raise Stopped('busy', 'This job is open in the Codex app. Carry on there, or close it there and ask again.') from None
            else:
                thread = call('thread/start', {'cwd': str(folder)})
            thread_id = str(((thread.get('thread') or {}).get('id')) or ('' if forked else resume) or '')[:120]
            if not thread_id:
                raise Stopped('failed', 'Codex did not start a thread')
            on_session(thread_id)
            if forked:
                on_progress('It was open in the Codex app, so this goes on in a copy of the thread there')
            if not resume or forked:
                name = job_name(job)
                call('thread/name/set', {'threadId': thread_id, 'name': name})
            call('turn/start', {'threadId': thread_id, 'input': [{'type': 'text', 'text': prompt}]})
            until_done()
        except (EOFError, OSError, ValueError) as error:
            raise stopped() or Stopped('failed', state['error'] or (str(error) if isinstance(error, ValueError) else '') or
                                        Local.stderr_hint(log.name) or 'Codex closed before it finished, without saying why') from None
        return self.outcome(state, stopped, thread_id)

    def follow(self, chan, flags, log, state, job, on_progress, timeout):
        read, call, stopped, until_done = self.rpc(chan, flags, log, state, on_progress, timeout)
        thread_id = str(job.get('session_id') or '')
        if not thread_id:
            raise Stopped('restart', RESTARTED)
        try:
            call('initialize', {'clientInfo': {'name': 'the_machine', 'title': 'The Machine', 'version': '1'}})
            chan.send({'jsonrpc': '2.0', 'method': 'initialized'})
            thread = call('thread/resume', {'threadId': thread_id}).get('thread') or {}
            turns = [t for t in thread.get('turns') or [] if isinstance(t, dict)]
            last = turns[-1] if turns else {}
            if str(last.get('status') or '') == 'inProgress':
                on_progress('Carrying on after The Machine restarted')
                until_done()
            else:  # it ended while The Machine was down: its answer is in the thread
                state['status'] = str(last.get('status') or 'failed')
                error = last.get('error') if isinstance(last.get('error'), dict) else {}
                state['error'] = str(error.get('message') or '')[:500]
                said = [str(i.get('text') or '').strip() for i in last.get('items') or [] if isinstance(i, dict) and i.get('type') == 'agentMessage']
                state['summary'] = next((t for t in reversed(said) if t), '')
        except (EOFError, OSError, ValueError):
            raise stopped() or Stopped('restart', RESTARTED) from None
        return self.outcome(state, stopped, thread_id)

    @staticmethod
    def handle(m, send, state, on_progress):
        method, params = m.get('method'), m.get('params') if isinstance(m.get('params'), dict) else {}
        if method and 'id' in m:
            # A question for whoever runs the turn. Nobody is there to answer it here, as with `codex exec`: an
            # approval is declined (the agent carries on without it), anything else is not supported.
            if method.endswith('requestApproval') or method in ('applyPatchApproval', 'execCommandApproval'):
                send({'jsonrpc': '2.0', 'id': m['id'], 'result': {'decision': 'decline' if method.endswith('requestApproval') else 'denied'}})
                on_progress('Declined a step that needed approval')
            elif method == 'mcpServer/elicitation/request':
                send({'jsonrpc': '2.0', 'id': m['id'], 'result': {'action': 'decline'}})
            else:
                send({'jsonrpc': '2.0', 'id': m['id'], 'error': {'code': -32601, 'message': 'Not supported by The Machine'}})
            return
        item = params.get('item') if isinstance(params.get('item'), dict) else {}
        if method == 'item/started' and item.get('type') == 'commandExecution':
            on_progress(('Running ' + str(item.get('command') or ''))[:160])
        elif method == 'item/completed' and item.get('type') == 'fileChange':
            names = [Path(str(c.get('path') or '')).name for c in item.get('changes') or [] if isinstance(c, dict)]
            if any(names):
                on_progress(('Changed ' + ', '.join(n for n in names if n))[:160])
        elif method == 'item/completed' and item.get('type') == 'agentMessage':
            text = str(item.get('text') or '').strip()
            if text:
                state['summary'] = text
                on_progress(text.splitlines()[0][:160])
        elif method == 'error':
            error = params.get('error') if isinstance(params.get('error'), dict) else {}
            state['error'] = str(error.get('message') or params.get('message') or '')[:500]
        elif method == 'turn/completed':
            turn = params.get('turn') if isinstance(params.get('turn'), dict) else {}
            state['status'] = str(turn.get('status') or 'completed')
            error = turn.get('error') if isinstance(turn.get('error'), dict) else {}
            if error.get('message'):
                state['error'] = str(error['message'])[:500]


def transcript_answer(path, work=None):
    """(summary, files) of one turn from its own transcript (turn-N.jsonl): what the job said last, as `result` saves
    it (Claude Code's result, Codex's last message, from `codex exec` or the app server), and the files it wrote that
    are still in its folder. None when the turn has no answer there (it stopped, or it ran in the background)."""
    summary, failed, files = None, False, []
    try:
        lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return None
    for raw in lines:
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind, params = event.get('type'), event.get('params') if isinstance(event.get('params'), dict) else {}
        item = event.get('item') if isinstance(event.get('item'), dict) else params.get('item') if isinstance(params.get('item'), dict) else {}
        changed = []
        if kind == 'result':  # Claude Code -p
            if event.get('is_error') or event.get('subtype') not in (None, 'success'):
                failed = True
            elif str(event.get('result') or '').strip():
                summary = str(event['result'])
        elif kind == 'assistant':
            for part in ((event.get('message') or {}).get('content') or []) if isinstance(event.get('message'), dict) else []:
                if isinstance(part, dict) and part.get('type') == 'tool_use' and part.get('name') in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
                    changed.append((part.get('input') or {}).get('file_path') or (part.get('input') or {}).get('notebook_path'))
        elif (kind == 'item.completed' and item.get('type') == 'agent_message') or (event.get('method') == 'item/completed' and item.get('type') == 'agentMessage'):
            if str(item.get('text') or '').strip():
                summary = str(item['text']).strip()
        elif kind in ('turn.failed', 'error') or event.get('method') == 'turn/completed' and str((params.get('turn') or {}).get('status') or 'completed') != 'completed':
            failed = True
        if item.get('type') in ('file_change', 'fileChange'):
            changed += [c.get('path') for c in item.get('changes') or [] if isinstance(c, dict)]
        for name in changed:
            if work and isinstance(name, str) and name:
                found = Path(name) if Path(name).is_absolute() else Path(work) / name
                try:
                    relative = str(found.resolve().relative_to(Path(work).resolve()))
                except (ValueError, OSError):
                    continue
                if relative not in files and found.is_file() and not found.is_symlink():
                    files.append(relative)
    if failed or not summary:
        return None
    return summary[:SUMMARY_MAX], files[:FILES_MAX]


def ms(value):
    try:
        found = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return 0
    return int((found if found.tzinfo else found.replace(tzinfo=dt.timezone.utc)).timestamp() * 1000)


def turn_answers(job, folder=None):
    """Each turn's answer of a job, in turn order: [{'turn', 'summary', 'files', 'at'}]. Turn 1 is the handoff, turn
    N the (N-1)th change asked of it. What `result` saved comes first; a turn it has no answer for (jobs from before
    answers were kept saved only their latest, the summary) is read from that turn's own transcript in the job folder.
    An answer always comes after the message it answers."""
    def listed(key):
        try:
            found = json.loads(job.get(key) or '[]')
        except (ValueError, TypeError):
            return []
        return [x for x in found if isinstance(x, dict)] if isinstance(found, list) else []
    asks = [x for x in listed('asks') if str(x.get('text') or '').strip()]
    turns = max(int(job.get('turns') or 1), len(asks) + 1)
    prompts = [ms(job.get('created_at'))] + [ms(x.get('at')) for x in asks]
    prompts += [prompts[-1]] * (turns - len(prompts))
    saved = [x for x in listed('answers') if str(x.get('summary') or '').strip()]
    if not saved and job.get('status') == 'result' and str(job.get('summary') or '').strip():
        saved = [{'summary': job['summary'], 'files': [f.get('path') for f in listed('files')], 'at': job.get('finished_at') or job.get('updated_at'),
                  'turn': turns}]
    found = {}
    for x in saved:
        at = ms(x.get('at'))
        turn = x.get('turn') if isinstance(x.get('turn'), int) and 1 <= x['turn'] <= turns else None
        if turn is None:  # saved before answers had their turn: the last message sent before it
            turn = max([n + 1 for n, p in enumerate(prompts) if p and p <= at] or [turns])
        found[turn] = {'turn': turn, 'summary': str(x['summary']), 'at': at,
                       'files': [str(f) for f in x.get('files') or [] if isinstance(f, str)][:FILES_MAX]}
    if folder:
        work = Path(folder) / 'work'
        for turn in range(1, turns + 1):
            path = Path(folder) / f'turn-{turn}.jsonl'
            if turn in found or not path.is_file():
                continue
            read = transcript_answer(path, work)
            if read:
                try:
                    at = int(path.stat().st_mtime * 1000)  # its last line: when the turn ended
                except OSError:
                    at = 0
                found[turn] = {'turn': turn, 'summary': read[0], 'files': read[1], 'at': at}
    out = []
    for turn in sorted(found):
        answer = found[turn]
        answer['at'] = max(answer['at'], prompts[turn - 1] + 1)
        if out:
            answer['at'] = max(answer['at'], out[-1]['at'] + 1)
        out.append(answer)
    return out


def app_link(runner, session):
    """Where the owner opens the job in their own app: the Codex app's thread, the Claude Code session's page."""
    if isinstance(runner, AppServerCodex) and re.fullmatch(r'[0-9a-f-]{36}', str(session or '')):
        return 'codex://threads/' + session
    if hasattr(runner, 'link'):  # a Claude Code session (background or remote), a Codex cloud task
        try:
            return runner.link(session)
        except (OSError, subprocess.SubprocessError):
            return 'https://claude.ai/code' if isinstance(runner, BackgroundClaude) else None
    return None


APP_LINK = re.compile(r'(https://\S+|codex://threads/[0-9a-f-]{36})')
_HOST = {'at': 0.0, 'found': None}


def codex_host_id(home=None):
    """This computer as the Codex app on the owner's other devices knows it, when the Codex app here has remote
    connections on: `remote-control:<environment id>` from the app's own state (read only). None otherwise."""
    if home is None and time.monotonic() - _HOST['at'] < 60:
        return _HOST['found']
    base = Path(home or os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    found = None
    for path in sorted(base.glob('state_*.sqlite'), key=lambda p: int(re.sub(r'\D', '', p.stem) or 0), reverse=True):
        try:
            with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=1) as db:
                row = db.execute('SELECT environment_id FROM remote_control_enrollments WHERE remote_control_enabled=1 '
                                 'AND environment_id IS NOT NULL ORDER BY updated_at DESC LIMIT 1').fetchone()
        except sqlite3.Error:
            continue
        if row and re.fullmatch(r'[A-Za-z0-9_-]{4,128}', str(row[0] or '')):
            found = 'remote-control:' + row[0]
        break
    if home is None:
        _HOST.update(at=time.monotonic(), found=found)
    return found


def elsewhere_link(url, host=None):
    """The Codex app link that opens the thread on this computer from the owner's other devices: a plain
    codex://threads/<id> opens it on the device it is clicked on, where a thread The Machine just started isn't
    known yet ("no rollout found"). With ?hostId= the app opens it on this computer through its remote connection."""
    host = host or codex_host_id()
    if not host or not re.fullmatch(r'codex://threads/[0-9a-f-]{36}', str(url or '')):
        return None
    return url + '?' + urllib.parse.urlencode({'hostId': host})


# ------------------------------------------------------------------ the jobs

class AgentJobs:
    def __init__(self, connect, config, root, card=None, close_card=None, revise=None, network=None):
        """`connect`: the Store's connection. `root`: where job folders go. `card(suggestion_id)` gives what a brief is
        made of ({card, drafts, links, files, messages, task, note, about, history}); `close_card(suggestion_id)` marks the card done;
        `revise(suggestion_id, text)` puts a job's rewritten message in the card's draft (True when it did);
        `network(body)` posts to the Network's /api/fleet/agent-results (remote Claude jobs, agent_remote.py)."""
        self.connect, self.config, self.root = connect, config, Path(root)
        self.card, self.close_card, self.revise, self._network = card, close_card, revise, network
        own = settings(config) or {}
        self.limit = threading.BoundedSemaphore(max(1, min(int(own.get('max_parallel') or 2), 8)))
        self.timeout = max(60, min(int(float(own.get('timeout_minutes') or 30) * 60), 6 * 3600))
        self.procs, self.lock = {}, threading.Lock()
        with self.connect() as db:
            db.executescript(SCHEMA)
            have = {r[1] for r in db.execute('PRAGMA table_info(agent_jobs)')}
            if 'draft_updated' not in have:
                db.execute('ALTER TABLE agent_jobs ADD COLUMN draft_updated INTEGER')
            if 'answers' not in have:  # each turn's answer, so the card's chat shows every reply (older jobs only kept the latest)
                db.execute('ALTER TABLE agent_jobs ADD COLUMN answers TEXT')

    # -- what the dashboard shows
    def tools(self):
        return tools(self.config)

    def tool(self, tool_id):
        found = next((t for t in self.tools() if t['id'] == tool_id), None)
        if not found:
            raise ValueError(f'{TOOLS.get(tool_id, "That tool")} is not available on this Machine')
        return found

    def view(self, row):
        files = json.loads(row['files'] or '[]')
        asks = json.loads(row['asks'] or '[]')
        out = {k: row[k] for k in ('id', 'tool', 'place', 'status', 'progress', 'summary', 'error', 'error_kind', 'turns', 'outcome',
                                   'created_at', 'updated_at', 'started_at', 'finished_at')}
        out['draft_updated'] = bool(row['draft_updated'])
        out['can_follow_up'] = bool(row['session_id']) and row['place'] != 'cloud'
        url = row['url'] if APP_LINK.fullmatch(row['url'] or '') else None
        out.update(name=TOOLS.get(row['tool'], row['tool']), files=files, has_diff=bool(row['diff']), url=url, url_elsewhere=elsewhere_link(url),
                   app=('Open in Codex' if url.startswith('codex://') else 'Open in Codex cloud' if row['place'] == 'cloud'
                        else 'Open in Claude' if 'claude.ai' in url else 'Open') if url else None,
                   links=list(dict.fromkeys(URL.findall(row['summary'] or '')))[:10], ask=asks[-1]['text'] if asks else None)
        if row['place'] == 'remote':
            out['device'] = str(((settings(self.config) or {}).get('claude') or {}).get('device') or 'your computer')[:60]
        return out

    def latest(self, db, suggestion_ids):
        ids = [str(i) for i in suggestion_ids]
        rows = {}
        for n in range(0, len(ids), 500):
            chunk = ids[n:n + 500]
            for r in db.execute(f"SELECT * FROM agent_jobs WHERE suggestion_id IN ({','.join('?' * len(chunk))}) ORDER BY created_at", chunk):
                rows[r['suggestion_id']] = r
        return rows

    def annotate(self, db, suggestions):
        rows = self.latest(db, [s['id'] for s in suggestions])
        for s in suggestions:
            if s['id'] in rows:
                s['agent_job'] = self.view(rows[s['id']])

    def live(self):
        """The jobs still going and those finished in the last minutes, for the dashboard to follow without a full refresh."""
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)).isoformat()
        with self.connect() as db:
            rows = db.execute("SELECT * FROM agent_jobs WHERE status IN ('sent','running','waiting') OR updated_at>=? ORDER BY created_at", (since,)).fetchall()
        return {'jobs': {r['suggestion_id']: self.view(r) for r in rows}}

    # -- what the owner does
    def draft(self, suggestion_id):
        parts = self.card(str(suggestion_id)) if self.card else None
        if not parts:
            raise ValueError('No such card')
        title = str(parts['card'].get('title') or 'this task').strip().rstrip('.')
        return {'suggestion_id': str(suggestion_id), 'tools': self.tools(), 'gets': gets_for(parts),
                'line': f'Picks up “{title[:160]}” and reports back here. Sends nothing itself.',
                'brief': brief_for(parts['card'], parts.get('drafts') or (), parts.get('links') or (), parts.get('files') or (), parts.get('messages') or '',
                                   task=parts.get('task'), note=parts.get('note'), about=parts.get('about') or (), history=parts.get('history') or (),
                                   sources=parts.get('sources') or (), inline=any(t['place'] != 'local' for t in self.tools()))}

    def start(self, suggestion_id, tool_id, brief, note=None):
        tool = self.tool(tool_id)
        if len(str(note or '')) > ASK_MAX:
            raise ValueError('The note is too long')
        brief = with_note(str(brief or '').strip(), note) if str(brief or '').strip() else ''
        if not brief:
            raise ValueError('Write what the job is')
        if len(brief) > BRIEF_MAX:
            raise ValueError('The brief is too long')
        parts = self.card(str(suggestion_id)) if self.card else None
        if not parts:
            raise ValueError('No such card')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            busy = db.execute(f"SELECT tool FROM agent_jobs WHERE suggestion_id=? AND status IN ({','.join('?' * len(ACTIVE))})",
                              (str(suggestion_id), *ACTIVE)).fetchone()
            if busy:
                raise ValueError(f'{TOOLS.get(busy["tool"])} is already on this card')
            job_id = 'aj_' + secrets.token_hex(8)
            at = now()
            # Anything earlier on this card that the owner didn't close was handed off again.
            db.execute("UPDATE agent_jobs SET outcome='again',updated_at=? WHERE suggestion_id=? AND outcome IS NULL", (at, str(suggestion_id)))
            db.execute('INSERT INTO agent_jobs(id,suggestion_id,tool,place,status,brief,asks,turns,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (job_id, str(suggestion_id), tool['id'], tool['place'], 'sent', brief, '[]', 1, at, at))
        try:
            self.prepare_folder(job_id, brief, parts)
        except OSError as error:
            self.finish(job_id, 'failed', error='I could not make the job folder', error_kind='folder')
            LOG.error('agent job folder error=%s', type(error).__name__)
            return self.get(job_id)
        self.launch(job_id, brief, resume=None)
        return self.get(job_id)

    def follow_up(self, job_id, text):
        text = str(text or '').strip()
        if not text:
            raise ValueError('Say what should change')
        if len(text) > ASK_MAX:
            raise ValueError('That is too long')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM agent_jobs WHERE id=?', (str(job_id),)).fetchone()
            if not row:
                raise ValueError('No such job')
            if row['status'] in ACTIVE:
                raise ValueError('It is still working on it')
            if not row['session_id']:
                raise ValueError('This job has no session to continue. Hand it off again instead.')
            if row['outcome'] in ('done', 'again'):
                raise ValueError('This job is closed')
            self.tool(row['tool'])
            asks = json.loads(row['asks'] or '[]') + [{'at': now(), 'text': text}]
            db.execute("UPDATE agent_jobs SET status='sent',asks=?,turns=turns+1,progress=NULL,error=NULL,error_kind=NULL,outcome=NULL,"
                       'updated_at=?,started_at=NULL,finished_at=NULL WHERE id=?', (json.dumps(asks[-20:], ensure_ascii=False), now(), row['id']))
        self.launch(row['id'], text, resume=row['session_id'])
        return self.get(row['id'])

    def retry(self, job_id):
        """A job that stopped: it carries on in its own session (thread) when it has one, with the change asked last
        or, for the handoff itself, a note to carry on where it left off. Without a session, or when its thread is
        gone, the brief runs again from the start."""
        with self.connect() as db:
            row = db.execute('SELECT * FROM agent_jobs WHERE id=?', (str(job_id),)).fetchone()
        if not row or row['status'] not in ('failed', 'cancelled'):
            raise ValueError('Nothing to try again')
        self.tool(row['tool'])
        asks = json.loads(row['asks'] or '[]')
        resume = row['session_id'] if row['place'] == 'local' or asks else None
        prompt = (asks[-1]['text'] if asks else CARRY_ON) if resume else row['brief']
        with self.connect() as db:
            db.execute("UPDATE agent_jobs SET status='sent',progress=NULL,error=NULL,error_kind=NULL,outcome=NULL,updated_at=?,started_at=NULL,finished_at=NULL WHERE id=?",
                       (now(), row['id']))
        self.launch(row['id'], prompt, resume=resume, fresh=None if asks else row['brief'])
        return self.get(row['id'])

    def cancel(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM agent_jobs WHERE id=?', (str(job_id),)).fetchone()
        if not row or row['status'] not in ACTIVE:
            raise ValueError('Nothing to cancel')
        runner = self.runner(row)
        if hasattr(runner, 'cancel'):
            runner.cancel(row)  # a cloud task is stopped where it runs, if it can be
        with self.lock:
            proc = self.procs.get(row['id'])
            if proc:
                proc.cancelled = True
        if proc:
            stop_proc(proc)
        self.finish(row['id'], 'cancelled', error='Cancelled', error_kind='cancelled', only_if=ACTIVE)
        return self.get(row['id'])

    def set_outcome(self, job_id, outcome):
        if outcome not in ('done', 'set_aside'):
            raise ValueError('Unknown outcome')
        with self.connect() as db:
            row = db.execute('SELECT * FROM agent_jobs WHERE id=?', (str(job_id),)).fetchone()
            if not row or row['status'] in ACTIVE:
                raise ValueError('Nothing to close')
            db.execute('UPDATE agent_jobs SET outcome=?,updated_at=? WHERE id=?', (outcome, now(), row['id']))
        return self.get(row['id'])

    def card_closed(self, suggestion_id):
        """The owner marked the card done: its job's result was what they needed (learning reads the outcome)."""
        with self.connect() as db:
            db.execute("UPDATE agent_jobs SET outcome='done',updated_at=? WHERE suggestion_id=? AND outcome IS NULL AND status='result'",
                       (now(), str(suggestion_id)))

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM agent_jobs WHERE id=?', (str(job_id),)).fetchone()
        if not row:
            raise ValueError('No such job')
        return self.view(row)

    def diff(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT diff FROM agent_jobs WHERE id=?', (str(job_id),)).fetchone()
        if not row or not row['diff']:
            raise ValueError('No changes to show')
        return row['diff']

    def file(self, job_id, relative):
        """A file the job made or changed, only one its result lists and only inside its own folder."""
        with self.connect() as db:
            row = db.execute('SELECT files FROM agent_jobs WHERE id=?', (str(job_id),)).fetchone()
        if not row or relative not in [f['path'] for f in json.loads(row['files'] or '[]')]:
            raise ValueError('Not a file of this job')
        folder = self.work(str(job_id)).resolve()
        found = (folder / relative)
        if found.is_symlink() or not found.resolve().is_relative_to(folder) or not found.is_file():
            raise ValueError('Not a file of this job')
        if found.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('This file is too large to open here')
        return found

    # -- running
    def folder(self, job_id):
        if not re.fullmatch(r'aj_[0-9a-f]{16}', str(job_id)):
            raise ValueError('No such job')
        return self.root / str(job_id)

    def work(self, job_id):
        return self.folder(job_id) / 'work'

    def prepare_folder(self, job_id, brief, parts):
        work = self.work(job_id)
        work.mkdir(parents=True, exist_ok=False, mode=0o700)
        (work / 'BRIEF.md').write_text(brief + '\n')
        card = work / 'card'
        for source in parts.get('files') or ():
            source = Path(source)
            if source.is_file() and not source.is_symlink() and source.stat().st_size <= 20 * 1024 * 1024:
                card.mkdir(exist_ok=True, mode=0o700)
                shutil.copyfile(source, card / source.name)
        if parts.get('messages'):
            card.mkdir(exist_ok=True, mode=0o700)
            (card / 'messages.md').write_text(parts['messages'])
        # A Claude Code background session would move into a git worktree of the folder before editing; here the
        # folder is the job's own, so it edits in place (worktree.bgIsolation, a project setting of this folder only).
        (work / '.claude').mkdir(exist_ok=True, mode=0o700)
        (work / '.claude' / 'settings.json').write_text(json.dumps({'worktree': {'bgIsolation': 'none'}}) + '\n')
        if shutil.which('git'):
            try:
                git(work, 'init', '-q')
                git(work, 'add', '-A')
                git(work, 'commit', '-q', '--allow-empty', '-m', 'The card, as handed off')
            except (subprocess.SubprocessError, OSError) as error:
                LOG.warning('agent job git error=%s', type(error).__name__)
                shutil.rmtree(git_dir(work), ignore_errors=True)

    def runner(self, row):
        own = (settings(self.config) or {}).get(row['tool']) or {}
        command = find(row['tool'], own.get('command') if isinstance(own, dict) else None)
        if not command:
            raise Stopped('missing', f'{TOOLS[row["tool"]]} is not installed here any more')
        if row['place'] == 'local':
            # Where the owner also sees it: a Claude Code background session, a thread in the Codex app. The plain
            # `claude -p` / `codex exec` runs stay for when those can't start (an untrusted folder, an older Codex)
            # or when the config says `"background": false` / `"app_server": false`.
            if row['tool'] == 'claude':
                home = own.get('config_dir') if isinstance(own.get('config_dir'), str) else None
                return BackgroundClaude(command, home) if own.get('background', True) is not False else LocalClaude(command, home)
            server = None if own.get('command') else desktop_codex()
            if own.get('app_server', True) is False:
                return LocalCodex(command, bool(shutil.which('git')))
            return AppServerCodex(command, server, detach=own.get('detach', True) is not False and can_detach())
        import agent_remote  # phase 2: the owner's paired computer (Claude) or Codex cloud
        return agent_remote.runner(row, command, own, self)

    def launch(self, job_id, prompt, resume, fresh=None):
        threading.Thread(target=self.turn, args=(job_id, prompt, resume, fresh), daemon=True, name='agent-job').start()

    def turn(self, job_id, prompt, resume, fresh=None):
        """One turn. `fresh`: the brief to start over with when the session it resumes is gone (Try again)."""
        with self.limit:
            with self.connect() as db:
                row = db.execute('SELECT * FROM agent_jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['status'] != 'sent':
                return  # cancelled while it waited for a free slot
            try:
                runner = self.runner(row)
                with self.connect() as db:
                    db.execute("UPDATE agent_jobs SET status=?,started_at=COALESCE(started_at,?),updated_at=? WHERE id=? AND status='sent'",
                               ('waiting' if row['place'] == 'remote' else 'running', now(), now(), job_id))
                turn_no = row['turns']
                transcript = self.folder(job_id) / f'turn-{turn_no}.jsonl'
                try:
                    session, summary = self.run_turn(runner, row, prompt, resume, transcript)
                except Stopped as stop:
                    if stop.kind != 'gone' or not fresh:
                        raise
                    with self.connect() as db:
                        db.execute('UPDATE agent_jobs SET session_id=NULL,url=NULL WHERE id=?', (job_id,))
                    self.progress(job_id, 'Its thread was gone, so it starts again from the brief')
                    session, summary = self.run_turn(runner, row, fresh, None, transcript)
            except Stopped as stop:
                self.finish(job_id, 'cancelled' if stop.kind == 'cancelled' else 'failed', error=str(stop)[:500], error_kind=stop.kind)
                return
            except Exception as error:  # noqa: BLE001 - a job never takes the service down
                LOG.error('agent job error=%s', type(error).__name__)
                self.finish(job_id, 'failed', error='Something went wrong running it', error_kind='internal')
                return
            if summary is None:
                return  # a remote job: its result comes back through the connector
            self.result(job_id, session, summary)

    def run_turn(self, runner, row, prompt, resume, transcript):
        job_id = row['id']
        own = (settings(self.config) or {}).get(row['tool']) or {}
        on_progress = lambda line: self.progress(job_id, line)

        def on_session(sid, runner=runner):
            self.session(job_id, sid, app_link(runner, sid))
        try:
            return runner.run(self.named(row), prompt, resume, self.work(job_id), transcript, self.timeout, on_progress, on_session, self.procs)
        except (Untrusted, NoAppServer) as why:
            LOG.info('agent job fallback=%s', type(why).__name__)
            command = runner.command
            plain = LocalClaude(command, getattr(runner, 'config_dir', None)) if isinstance(why, Untrusted) else LocalCodex(command, bool(shutil.which('git')))
            with self.connect() as db:
                db.execute('UPDATE agent_jobs SET url=NULL WHERE id=?', (job_id,))
            return plain.run(self.named(row), prompt, resume, self.work(job_id), transcript, self.timeout, on_progress,
                             lambda sid: self.session(job_id, sid), self.procs)

    def named(self, row):
        job = dict(row)
        try:
            found = self.card(row['suggestion_id']) if self.card else None
        except Exception:  # noqa: BLE001 - the brief's first line names it then
            found = None
        job['card_title'] = ((found or {}).get('card') or {}).get('title') or ''
        return job

    def progress(self, job_id, line):
        with self.connect() as db:
            db.execute("UPDATE agent_jobs SET progress=?,status=CASE WHEN status='waiting' THEN 'running' ELSE status END,updated_at=? "
                       "WHERE id=? AND status IN ('running','waiting')", (str(line)[:200], now(), job_id))

    def session(self, job_id, session_id, link=None):
        with self.connect() as db:
            db.execute('UPDATE agent_jobs SET session_id=?,url=COALESCE(?,url),updated_at=? WHERE id=?', (str(session_id)[:120], link, now(), job_id))

    def changes(self, job_id):
        """What the job made or changed in its folder since it was handed off: (files, diff)."""
        work = self.work(job_id)
        if not git_dir(work).is_dir():
            files = [{'path': str(p.relative_to(work)), 'size': p.stat().st_size} for p in sorted(work.rglob('*'))
                     if p.is_file() and not p.is_symlink() and p.relative_to(work).parts[0] not in ('card', '.claude') and p.name != 'BRIEF.md']
            return files[:FILES_MAX], ''
        try:
            git(work, 'add', '-A')
            names = git(work, 'diff', '--cached', '--name-status', '--no-renames', 'HEAD').stdout.splitlines()
            diff = git(work, 'diff', '--cached', '--stat', '-p', 'HEAD').stdout
        except (subprocess.SubprocessError, OSError) as error:
            LOG.warning('agent job diff error=%s', type(error).__name__)
            return [], ''
        files = []
        for line in names:
            status, _, path = line.partition('\t')
            found = work / path
            if status != 'D' and found.is_file() and not found.is_symlink():
                files.append({'path': path, 'size': found.stat().st_size})
        if len(diff) > DIFF_MAX:
            diff = diff[:DIFF_MAX] + '\n… (cut here; the full changes are in the job folder)\n'
        return files[:FILES_MAX], diff

    def result(self, job_id, session, summary, files=None, diff=None):
        if files is None:
            files, diff = self.changes(job_id)
        updated = self.take_draft(job_id, files)
        summary, at = str(summary or 'Done, without a summary.')[:SUMMARY_MAX], now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT answers,turns FROM agent_jobs WHERE id=? AND status IN ('running','waiting','sent')", (job_id,)).fetchone()
            if not row:
                return
            try:
                answers = json.loads(row['answers'] or '[]')
            except ValueError:
                answers = []
            answers = (answers if isinstance(answers, list) else []) + [{'at': at, 'turn': row['turns'], 'summary': summary,
                                                                          'files': [f['path'] for f in files]}]
            db.execute("UPDATE agent_jobs SET status='result',session_id=COALESCE(?,session_id),summary=?,files=?,diff=?,progress=NULL,"
                       "error=NULL,error_kind=NULL,draft_updated=?,updated_at=?,finished_at=?,answers=? WHERE id=? AND status IN ('running','waiting','sent')",
                       (session, summary, json.dumps(files), diff or None, 1 if updated else None, at, at,
                        json.dumps(answers[-20:], ensure_ascii=False), job_id))

    def take_draft(self, job_id, files):
        """A message the job rewrote (draft.md, made or changed this turn) becomes the card's draft. The owner still
        reads it there and sends it with the card's own Send."""
        if not self.revise or DRAFT_FILE not in [f['path'] for f in files]:
            return False
        found = self.work(job_id) / DRAFT_FILE
        try:
            if found.is_symlink() or found.stat().st_size > 50_000:
                return False
            text = found.read_text(errors='replace').strip()
        except OSError:
            return False
        taken = self.folder(job_id) / 'draft.taken'  # only a draft.md that changed since the last turn took it
        digest = hashlib.sha256(text.encode()).hexdigest()
        try:
            if not text or taken.read_text().strip() == digest:
                return False
        except OSError:
            if not text:
                return False
        with self.connect() as db:
            row = db.execute('SELECT suggestion_id FROM agent_jobs WHERE id=?', (job_id,)).fetchone()
        try:
            done = bool(row and self.revise(row['suggestion_id'], text))
            if done:
                taken.write_text(digest + '\n')
            return done
        except Exception as error:  # noqa: BLE001 - the result still shows; the draft stays as it was
            LOG.warning('agent job draft error=%s', type(error).__name__)
            return False

    def finish(self, job_id, status, error=None, error_kind=None, only_if=None):
        with self.connect() as db:
            where = f" AND status IN ({','.join('?' * len(only_if))})" if only_if else ''
            db.execute(f'UPDATE agent_jobs SET status=?,error=?,error_kind=?,progress=NULL,updated_at=?,finished_at=? WHERE id=?{where}',
                       (status, error, error_kind, now(), now(), job_id, *(only_if or ())))

    def recover(self):
        """After a restart: a local job that was running has stopped with the service; one still waiting for a slot
        goes on. Remote and cloud jobs carry on where they run."""
        with self.connect() as db:
            running = db.execute("SELECT * FROM agent_jobs WHERE status='running' AND place='local'").fetchall()
            waiting = db.execute("SELECT * FROM agent_jobs WHERE status='sent'").fetchall()
            going = db.execute("SELECT * FROM agent_jobs WHERE status IN ('running','waiting') AND place!='local'").fetchall()
        for row in running:
            if row['tool'] == 'claude' and (row['url'] or '').startswith('https://claude.ai/code') and row['session_id']:
                # A Claude Code background session runs on without The Machine: follow it again.
                try:
                    runner = self.runner(row)
                    found = runner.entry(session=row['session_id']) if isinstance(runner, BackgroundClaude) else None
                except (Stopped, OSError, subprocess.SubprocessError):
                    found = None
                if found and found.get('id'):
                    threading.Thread(target=self.rewatch, args=(runner, dict(row), found['id']), daemon=True, name='agent-job').start()
                    continue
            live = read_server(self.folder(row['id'])) if row['tool'] == 'codex' else None
            if live and row['session_id'] and unit_active(live['unit']):
                # Its Codex app server ran on in a unit of its own: follow the turn there again.
                threading.Thread(target=self.rejoin, args=(dict(row), live), daemon=True, name='agent-job').start()
                continue
            if live:
                stop_unit(live['unit'])
                (self.folder(row['id']) / SERVER_FILE).unlink(missing_ok=True)
            self.finish(row['id'], 'failed', error=RESTARTED, error_kind='restart')
        for row in waiting:
            asks = json.loads(row['asks'] or '[]')
            self.launch(row['id'], asks[-1]['text'] if asks and row['session_id'] else row['brief'], row['session_id'] if asks else None)
        for row in going:
            try:
                runner = self.runner(row)
            except Stopped:
                continue
            if hasattr(runner, 'resume_watch'):
                runner.resume_watch(row)

    def rejoin(self, row, live):
        job_id = row['id']
        with self.limit:
            try:
                runner = self.runner(row)
                if not isinstance(runner, AppServerCodex):
                    raise Stopped('restart', RESTARTED)
                started = dt.datetime.fromisoformat(row['started_at']).timestamp() if row['started_at'] else time.time()
                left = max(60, self.timeout - (time.time() - started))
                transcript = self.folder(job_id) / f'turn-{row["turns"]}.jsonl'
                session, summary = runner.rejoin(self.named(row), live, transcript, left, lambda line: self.progress(job_id, line), self.procs)
            except Stopped as stop:
                self.finish(job_id, 'cancelled' if stop.kind == 'cancelled' else 'failed', error=str(stop)[:500], error_kind=stop.kind)
                return
            except Exception as error:  # noqa: BLE001 - a job never takes the service down
                LOG.error('agent job rejoin error=%s', type(error).__name__)
                self.finish(job_id, 'failed', error='Something went wrong following it after a restart', error_kind='internal')
                return
            self.result(job_id, session, summary)

    def rewatch(self, runner, row, short):
        try:
            since = dt.datetime.fromisoformat(row['started_at']).timestamp() if row['started_at'] else time.time()
            session, summary = runner.watch(row, short, since, self.timeout, lambda line: self.progress(row['id'], line),
                                            lambda sid: self.session(row['id'], sid), self.procs)
        except Stopped as stop:
            self.finish(row['id'], 'cancelled' if stop.kind == 'cancelled' else 'failed', error=str(stop)[:500], error_kind=stop.kind)
            return
        except Exception as error:  # noqa: BLE001
            LOG.error('agent job error=%s', type(error).__name__)
            self.finish(row['id'], 'failed', error='Something went wrong following it', error_kind='internal')
            return
        self.result(row['id'], session, summary)

    # -- remote results (agent_remote.py): what a remote Claude session reported through The Machine's connector
    def network(self, body):
        if not self._network:
            raise ValueError('This Machine is not on The Machine Network')
        return self._network(body)

    def remote_result(self, job_id, found):
        """A result the job's own session reported (the Network matched it to this job's ticket): its summary, links,
        diff, and a finished message for the card's draft."""
        with self.connect() as db:
            row = db.execute('SELECT * FROM agent_jobs WHERE id=?', (str(job_id),)).fetchone()
        if not row or row['status'] not in ('waiting', 'running'):
            return False
        summary = str(found.get('summary') or '').strip() or 'Done, without a summary.'
        links = '\n'.join(u for u in (found.get('links') or []) if isinstance(u, str) and u.startswith('https://'))[:4000]
        diff = str(found.get('diff') or '')[:DIFF_MAX]
        files = sorted(set(re.findall(r'^\+\+\+ b/(.+)$', diff, re.M)))[:FILES_MAX]
        self.result(row['id'], None, (summary + ('\n\n' + links if links else ''))[:SUMMARY_MAX],
                    files=[{'path': f, 'size': 0} for f in files], diff=diff)
        draft = str(found.get('draft') or '').strip()
        if draft and self.revise:
            try:
                if self.revise(row['suggestion_id'], draft):
                    with self.connect() as db:
                        db.execute('UPDATE agent_jobs SET draft_updated=1 WHERE id=?', (row['id'],))
            except Exception as error:  # noqa: BLE001 - the result still shows; the draft stays as it was
                LOG.warning('agent job draft error=%s', type(error).__name__)
        return True
