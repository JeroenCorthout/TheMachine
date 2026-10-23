"""Hand to Claude Code / Hand to Codex (agent_jobs.py): with stand-in `claude` and `codex` commands that do what the
real ones do (a background session with `claude agents --json`, the Codex app server's JSON-RPC, and the plain
stream-json / exec --json runs they fall back to). No real CLI, no network."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

import agent_jobs
import test_ux as ux
a = ux.a

FAKE_CLAUDE = r"""#!PYTHON
import datetime, json, os, signal, sys, time, uuid
from pathlib import Path
args = sys.argv[1:]
home = Path(os.environ['HOME'])
states = home / 'fake-claude'
states.mkdir(exist_ok=True)
def log(**more):
    open(os.environ['AJ_LOG'], 'a').write(json.dumps({'tool': 'claude', 'args': args, 'cwd': os.getcwd(), 'config_dir': os.environ.get('CLAUDE_CONFIG_DIR'), **more}) + '\n')
if args[:1] == ['agents']:
    print(json.dumps([json.loads(f.read_text()) for f in sorted(states.glob('*.json'))]))
    sys.exit(0)
if args[:1] == ['stop']:
    log()
    f = states / (args[1] + '.json')
    state = json.loads(f.read_text())
    state['state'] = 'stopped'
    f.write_text(json.dumps(state))
    try:
        os.kill(state['pid'], signal.SIGTERM)
    except ProcessLookupError:
        pass
    sys.exit(0)
if '--bg' in args:
    prompt = args[args.index('--') + 1]
    log(prompt=prompt)
    if os.environ.get('AJ_UNTRUSTED'):
        print('Workspace not trusted. Run `claude` in ' + os.getcwd() + ' once and accept the trust prompt, then retry.')
        sys.exit(1)
    session = args[args.index('--resume') + 1] if '--resume' in args else str(uuid.uuid4())
    short = session[:8]
    name = args[args.index('--name') + 1] if '--name' in args else 'resumed'
    f = states / (short + '.json')
    def put(**state):
        f.write_text(json.dumps({'id': short, 'kind': 'background', 'sessionId': session, 'name': name, 'cwd': os.getcwd(), 'pid': os.getpid(), **state}))
    if os.fork():
        time.sleep(0.1)
        print('backgrounded · ' + short + ' · ' + name)
        sys.exit(0)
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    put(state='working', detail='Writing summary.md')
    if 'sleep' in prompt:
        time.sleep(60)
    if 'fail' in prompt:
        put(state='failed', detail='It broke')
        sys.exit(0)
    if 'needs you' in prompt:
        put(state='blocked', detail='Allow Bash?')
        time.sleep(0.5)
    time.sleep(0.2)
    open('summary.md', 'w').write('Summary of the card\n' + ('More\n' if '--resume' in args else ''))
    if 'draft' in prompt:
        open('draft.md', 'w').write('Hi Sam,\n\nHere are the numbers.\n')
    project = Path(os.environ.get('CLAUDE_CONFIG_DIR') or home / '.claude') / 'projects' / '-job'
    project.mkdir(parents=True, exist_ok=True)
    at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
    with open(project / (session + '.jsonl'), 'a') as t:
        t.write(json.dumps({'type': 'user', 'timestamp': at, 'message': {'content': prompt}}) + '\n')
        t.write(json.dumps({'type': 'assistant', 'timestamp': at, 'message': {'content': [{'type': 'text', 'text': 'Wrote [summary.md](' + os.getcwd() + '/summary.md). See https://example.com/x'}]}}) + '\n')
    put(state='done', detail='wrote summary.md')
    sys.exit(0)
if '--cloud' in args:  # a message to an existing cloud session: queued there, nothing comes back here
    log(prompt=args[args.index('-p') + 1])
    print('Sent to ' + args[args.index('--cloud') + 1])
    sys.exit(0)
prompt = sys.stdin.read()
log(prompt=prompt)
if 'sleep' in prompt:
    time.sleep(60)
session = args[args.index('--resume') + 1] if '--resume' in args else 'sess-1'
print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': session}), flush=True)
print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Write', 'input': {'file_path': os.getcwd() + '/summary.md'}}]}, 'session_id': session}), flush=True)
if 'fail' in prompt:
    print(json.dumps({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'result': 'It broke', 'session_id': session}))
    sys.exit(1)
open('summary.md', 'w').write('Summary of the card\n' + ('More\n' if '--resume' in args else ''))
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'Wrote summary.md. See https://example.com/x', 'session_id': session}))
"""
THREAD = '01a16539-9d4a-7d83-9620-9203de5acb75'
FORK = '01a16590-3629-7943-9fcb-56c1b3647f45'
FAKE_CODEX = r"""#!PYTHON
import json, os, sys
args = sys.argv[1:]
def log(**more):
    open(os.environ['AJ_LOG'], 'a').write(json.dumps({'tool': 'codex', 'args': args, 'cwd': os.getcwd(), **more}) + '\n')
if args[:1] == ['app-server']:
    if os.environ.get('AJ_NO_APPSERVER'):
        sys.exit(2)
    out = lambda m: (sys.stdout.write(json.dumps(m) + '\n'), sys.stdout.flush())
    calls = []
    for line in sys.stdin:
        m = json.loads(line)
        if 'method' not in m:
            calls.append({'answer': m.get('result')})
            continue
        calls.append({'method': m['method'], 'params': m.get('params')})
        if 'id' not in m:
            continue
        method, params = m['method'], m.get('params') or {}
        if method == 'thread/start':
            out({'id': m['id'], 'result': {'thread': {'id': 'THREAD'}}})
        elif method == 'thread/resume' and os.environ.get('AJ_GONE'):  # its rollout was removed
            out({'id': m['id'], 'error': {'code': -32600, 'message': 'no rollout found for thread id ' + params['threadId']}})
        elif method == 'thread/resume' and os.environ.get('AJ_WRITER'):  # open in the Codex app
            out({'id': m['id'], 'error': {'code': -32600, 'message': 'thread ' + params['threadId'] + ' already has an active writer'}})
        elif method == 'thread/fork' and os.environ.get('AJ_WRITER') == 'nofork':
            out({'id': m['id'], 'error': {'code': -32600, 'message': 'no'}})
        elif method == 'thread/fork':
            out({'id': m['id'], 'result': {'thread': {'id': 'FORK_ID'}}})
        elif method == 'thread/resume':
            out({'id': m['id'], 'result': {'thread': {'id': params['threadId']}}})
        elif method == 'turn/start':
            prompt = params['input'][0]['text']
            out({'id': m['id'], 'result': {'turn': {'id': 't1', 'status': 'inProgress'}}})
            if 'approve' in prompt:
                out({'id': 99, 'method': 'item/commandExecution/requestApproval', 'params': {'command': 'rm -rf x'}})
                calls.append({'answer': json.loads(sys.stdin.readline()).get('result')})
            out({'method': 'item/started', 'params': {'item': {'type': 'commandExecution', 'command': 'printf hi > out.txt'}}})
            open('out.txt', 'w').write('hi')
            out({'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'Wrote out.txt.'}}})
            out({'method': 'turn/completed', 'params': {'turn': {'id': 't1', 'status': 'failed' if 'fail' in prompt else 'completed', 'error': {'message': 'It broke'} if 'fail' in prompt else None}}})
            log(calls=calls)
        else:
            out({'id': m['id'], 'result': {}})
    sys.exit(0)
TASK = 'task_e_0123456789abcdef0123456789abcdef'
if args[:2] == ['cloud', 'exec']:
    log(prompt=args[-1])
    print('Submitted ' + TASK + '\nhttps://chatgpt.com/codex/tasks/' + TASK)
    sys.exit(0)
if args[:2] == ['cloud', 'list']:
    print(json.dumps({'tasks': [{'id': TASK, 'status': 'ready', 'title': 'Fix the typo', 'environment_id': args[args.index('--env') + 1]}], 'cursor': None}))
    sys.exit(0)
if args[:2] == ['cloud', 'diff']:
    print('diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-teh\n+the')
    sys.exit(0)
prompt = sys.stdin.read()
log(prompt=prompt)
thread = args[args.index('resume') + 2] if 'resume' in args else 'thread-1'
print(json.dumps({'type': 'thread.started', 'thread_id': thread}), flush=True)
print(json.dumps({'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'printf hi > out.txt'}}), flush=True)
open('out.txt', 'w').write('hi')
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Wrote out.txt.'}}), flush=True)
print(json.dumps({'type': 'turn.completed', 'usage': {}}))
""".replace('FORK_ID', FORK).replace('THREAD', THREAD)

FAKE_SYSTEMD_RUN = r"""#!PYTHON
import json, os, sys
args = sys.argv[1:]
open(os.environ['AJ_LOG'], 'a').write(json.dumps({'tool': 'systemd-run', 'args': args}) + '\n')
out = next(a.split('append:', 1)[1] for a in args if a.startswith('--property=StandardOutput=append:'))
open(out, 'a').write('codex app-server (WebSockets)\n  listening on: ws://127.0.0.1:' + os.environ['AJ_WS_PORT'] + '\n')
"""
FAKE_SYSTEMCTL = r"""#!PYTHON
import json, os, sys
args = sys.argv[1:]
open(os.environ['AJ_LOG'], 'a').write(json.dumps({'tool': 'systemctl', 'args': args}) + '\n')
if 'show' in args:
    print('NoNewPrivileges=yes\nPrivateTmp=yes\nUMask=0077')
elif 'is-active' in args:
    print('active')
"""


class FakeWsAppServer:
    """`codex app-server --listen ws://127.0.0.1:0 --ws-auth capability-token`, as the job's own unit runs it: it lets
    in only the token whose hash it was started with, and the turn runs on with nobody connected."""
    def __init__(self, token_hash):
        import socket
        self.token_hash, self.calls, self.conns = token_hash, [], []
        self.started, self.done = threading.Event(), False
        self.server = socket.create_server(('127.0.0.1', 0))
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def close(self):
        self.server.close()
        import socket
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass

    def serve(self, conn):
        import hashlib, struct
        head = b''
        while b'\r\n\r\n' not in head:
            head += conn.recv(4096)
        token = next((l.split(b'Bearer ', 1)[1].strip() for l in head.split(b'\r\n') if l.lower().startswith(b'authorization:')), b'')
        if hashlib.sha256(token).hexdigest() != self.token_hash():
            conn.sendall(b'HTTP/1.1 401 Unauthorized\r\n\r\n')
            conn.close()
            return
        conn.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n')
        self.conns.append(conn)
        buf = [b'']
        def take(n):
            while len(buf[0]) < n:
                chunk = conn.recv(65536)
                if not chunk:
                    raise EOFError
                buf[0] += chunk
            out, buf[0] = buf[0][:n], buf[0][n:]
            return out
        def out(m):
            data = json.dumps(m).encode()
            n = len(data)
            conn.sendall(bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack('>H', n)) + data)
        def finish_turn():
            time.sleep(0.2)
            self.done = True
            out({'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'Done after the restart.'}}})
            out({'method': 'turn/completed', 'params': {'turn': {'id': 't1', 'status': 'completed'}}})
        try:
            while True:
                first, second = take(2)
                n = second & 0x7F
                if n == 126:
                    n = struct.unpack('>H', take(2))[0]
                mask = take(4)
                m = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(take(n))))
                self.calls.append({'method': m.get('method'), 'params': m.get('params')})
                if 'id' not in m:
                    continue
                method, params = m['method'], m.get('params') or {}
                if method == 'thread/start':
                    out({'id': m['id'], 'result': {'thread': {'id': THREAD}}})
                elif method == 'thread/resume':
                    turn = {'id': 't1', 'status': 'completed' if self.done else 'inProgress', 'items': []}
                    out({'id': m['id'], 'result': {'thread': {'id': params['threadId'], 'turns': [turn]}}})
                    if not self.done:
                        threading.Thread(target=finish_turn, daemon=True).start()
                elif method == 'turn/start':
                    out({'id': m['id'], 'result': {'turn': {'id': 't1', 'status': 'inProgress'}}})
                    self.started.set()  # and it runs on: the first connection never hears the end of it
                else:
                    out({'id': m['id'], 'result': {}})
        except (EOFError, OSError, ValueError):
            return


def job_session(jobs, job_id):
    with jobs.connect() as db:
        return db.execute('SELECT session_id FROM agent_jobs WHERE id=?', (job_id,)).fetchone()[0]


def wait(jobs, job_id, done=('result', 'failed', 'cancelled'), seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        job = jobs.get(job_id)
        if job['status'] in done:
            return job
        time.sleep(0.05)
    raise AssertionError('job still ' + jobs.get(job_id)['status'])


class AgentJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: [t.join(10) for t in threading.enumerate() if t.name == 'agent-job'])
        root = Path(self.tmp.name)
        self.bin = root / 'bin'
        self.bin.mkdir()
        for name, body in (('claude', FAKE_CLAUDE), ('codex', FAKE_CODEX), ('systemd-run', FAKE_SYSTEMD_RUN), ('systemctl', FAKE_SYSTEMCTL)):
            path = self.bin / name
            path.write_text(body.replace('PYTHON', sys.executable))
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.log = root / 'calls.jsonl'
        env = patch.dict(os.environ, {'PATH': f'{self.bin}{os.pathsep}{os.environ["PATH"]}', 'AJ_LOG': str(self.log), 'HOME': str(root / 'home')})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('CLAUDE_CONFIG_DIR', None)  # the owner's own; patch.dict puts it back
        (root / 'home').mkdir()
        app = patch.object(agent_jobs, 'DESKTOP_CODEX', ())  # never the real Codex app's codex
        app.start()
        self.addCleanup(app.stop)
        detach = patch.object(agent_jobs, 'can_detach', lambda: False)  # never a real systemd unit (DetachedCodex tests fake one)
        detach.start()
        self.addCleanup(detach.stop)
        agent_jobs._HOST.update(at=0.0, found=None)
        poll = patch.object(agent_jobs.BackgroundClaude, 'POLL', 0.05)
        poll.start()
        self.addCleanup(poll.stop)
        self.revised, self.sent, self.tickets, self.reported = [], [], {}, []
        self.store = a.Store(root / 'events.sqlite', protect=True)
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'm1', 'kind': 'gmail.message', 'occurred_at': '2026-10-20T08:00:00+00:00',
                                             'title': 'Quarterly numbers', 'body': 'Can you sum up the numbers?', 'deleted': False,
                                             'metadata': {'from': 'sam@client.example', 'thread_id': 't1', 'labels': ['INBOX'],
                                                          'source_url': 'https://mail.example/t1'}}])
        with self.store.connect() as db:
            event_id = db.execute('SELECT id FROM events').fetchone()[0]
        self.sid = self.store.suggest('numbers', 'Sum up the quarterly numbers', 'Sam asks for a summary.', [event_id])['id']
        self.config = {'database': str(root / 'events.sqlite'), 'accounts': [], 'agents': {'timeout_minutes': 1}}

    def jobs(self, **config):
        self.config['agents'].update(config)
        card = lambda sid: {'card': {'title': 'Sum up the quarterly numbers', 'body': 'Sam asks for a summary.'}, 'drafts': self.drafts,
                            'links': ['https://mail.example/t1'], 'files': [], 'messages': '## Quarterly numbers\nCan you sum up the numbers?'} if sid == self.sid else None
        return agent_jobs.AgentJobs(self.store.connect, self.config, Path(self.tmp.name) / 'agent-jobs', card=card,
                                    revise=lambda sid, text, job=None: self.revised.append((sid, text)) or True, network=self.network)

    def network(self, body):
        """The Network's /api/fleet/agent-results, as the control plane answers it."""
        self.sent.append(body)
        if body['action'] == 'register':
            self.tickets[body['ticket_hash']] = body['job']
            return {'ok': True, 'mcp_url': 'https://app.example.test/mcp'}
        if body['action'] == 'fetch':
            return {'results': [r for r in self.reported if r['ticket_hash'] in body['tickets']]}
        receipts = body.get('receipts') or [{'ticket_hash': h, 'report': 1} for h in body.get('tickets') or []]
        self.reported = [r for r in self.reported if not any(r['ticket_hash'] == a['ticket_hash'] and int(r.get('report') or 1) == a['report'] for a in receipts)]
        return {'ok': True, 'acked': receipts}

    drafts = []

    def calls(self, tool=None):
        found = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [c for c in found if tool is None or c['tool'] == tool]

    def test_only_tools_that_are_there_show(self):
        self.assertEqual([t['id'] for t in agent_jobs.tools(self.config)], ['claude', 'codex'])
        self.assertEqual(agent_jobs.tools({'agents': False}), [])
        self.assertEqual(agent_jobs.tools({'agents': {'codex': False}})[0]['id'], 'claude')
        with patch.dict(os.environ, {'PATH': '/nonexistent'}), patch.object(Path, 'home', return_value=Path('/nonexistent')):
            self.assertEqual(agent_jobs.tools({}), [])
        # A hosted Machine: Claude only with the owner's paired session, Codex only with its cloud environment.
        hosted = {'manage_url': 'https://app.example/m', 'agents': {}}
        self.assertEqual(agent_jobs.tools(hosted), [])
        hosted['agents'] = {'claude': {'session': 'cse_1', 'device': 'Laptop'}, 'codex': {'env': 'env_1'}}
        self.assertEqual(agent_jobs.tools(hosted), [{'id': 'claude', 'name': 'Claude Code', 'place': 'remote', 'device': 'Laptop'},
                                                    {'id': 'codex', 'name': 'Codex', 'place': 'cloud'}])

    def test_brief_has_the_task_links_files_and_what_done_looks_like(self):
        draft = self.jobs().draft(self.sid)
        self.assertIn('Sum up the quarterly numbers', draft['brief'])
        self.assertIn('https://mail.example/t1', draft['brief'])
        self.assertIn('card/messages.md', draft['brief'])
        self.assertIn('Done looks like', draft['brief'])
        # The hand-off sheet: one plain line and small chips for what it gets, never a link itself
        self.assertEqual(draft['line'], 'Picks up “Sum up the quarterly numbers” and reports back here. Sends nothing itself.')
        self.assertEqual([g['label'] for g in draft['gets']], ['1 message', 'messages.md', '1 link'])
        self.assertFalse(any('http' in g['label'] for g in draft['gets']))
        self.assertEqual(agent_jobs.gets_for({'message_kinds': ['email'] * 5, 'messages': 'x', 'drafts': [{'to_name': 'Robin Lee', 'body': 'Hi'}]}),
                         [{'kind': 'mail', 'label': '5 emails'}, {'kind': 'draft', 'label': 'Draft to Robin'}, {'kind': 'file', 'label': 'messages.md'}])
        self.assertEqual(agent_jobs.with_note('The job', ' Keep it short '), 'A note from me: Keep it short\n\nThe job')
        self.assertEqual(agent_jobs.with_note('The job', ''), 'The job')
        with self.assertRaises(ValueError):
            self.jobs().draft('nope')

    def test_claude_runs_as_a_background_session_and_a_follow_up_resumes_it(self):
        jobs = self.jobs()
        job = jobs.start(self.sid, 'claude', 'Summarize the card into summary.md')
        self.assertIn(job['status'], ('sent', 'running'))
        job = wait(jobs, job['id'])
        self.assertEqual(job['status'], 'result', job)
        self.assertIn('summary.md', job['summary'])
        self.assertEqual(job['links'], ['https://example.com/x'])
        self.assertEqual([f['path'] for f in job['files']], ['summary.md'])
        self.assertTrue(job['has_diff'])
        self.assertIn('+Summary of the card', jobs.diff(job['id']))
        # A named background session, so it shows in the owner's Claude apps: no permission mode, no bypass.
        call = self.calls('claude')[0]
        self.assertEqual(call['args'][:5], ['--bg', '--settings', '{"remoteControlAtStartup": true}', '--name', 'Sum up the quarterly numbers'])
        self.assertEqual(call['args'][-2:], ['--', 'Summarize the card into summary.md'])
        self.assertEqual(job['app'], 'Open in Claude')
        self.assertTrue(job['url'].startswith('https://claude.ai/code'))
        self.assertTrue(job['can_follow_up'])
        work = Path(call['cwd'])
        self.assertEqual(work.resolve(), (Path(self.tmp.name) / 'agent-jobs' / job['id'] / 'work').resolve())  # macOS: /var is /private/var
        self.assertEqual((work / 'BRIEF.md').read_text().strip(), 'Summarize the card into summary.md')
        self.assertIn('sum up the numbers', (work / 'card/messages.md').read_text())
        self.assertEqual(json.loads((work / '.claude/settings.json').read_text()), {'worktree': {'bgIsolation': 'none'}})
        self.assertFalse((work / '.git').exists())  # not a repository of its own, so the folder's trust carries over
        self.assertTrue((work.parent / 'git').is_dir())
        self.assertEqual(jobs.file(job['id'], 'summary.md'), (work / 'summary.md').resolve())
        for bad in ('../turn-1.jsonl', 'BRIEF.md', '/etc/passwd'):
            with self.assertRaises(ValueError):
                jobs.file(job['id'], bad)
        session = job['session_id'] if 'session_id' in job else None
        job = jobs.follow_up(job['id'], 'Add one more line')
        job = wait(jobs, job['id'])
        self.assertEqual(job['status'], 'result', job)
        self.assertEqual(job['turns'], 2)
        self.assertEqual(job['ask'], 'Add one more line')
        args = self.calls('claude')[1]['args']
        self.assertEqual(args[0], '--resume')
        self.assertNotIn('--name', args)
        self.assertIn('+More', jobs.diff(job['id']))
        with jobs.connect() as db:  # each turn's answer is kept, for the card's chat
            answers = json.loads(db.execute('SELECT answers FROM agent_jobs WHERE id=?', (job['id'],)).fetchone()['answers'])
        self.assertEqual(len(answers), 2)
        self.assertTrue(all(a['summary'] and a['at'] for a in answers))
        self.assertEqual([a['turn'] for a in answers], [1, 2])

    def test_claude_jobs_can_use_their_own_config_dir(self):
        other = Path(self.tmp.name) / 'claude-other'
        jobs = self.jobs(claude={'config_dir': str(other)})
        job = wait(jobs, jobs.start(self.sid, 'claude', 'Summarize the card into summary.md')['id'])
        self.assertEqual(job['status'], 'result', job)
        self.assertIn('summary.md', job['summary'])  # read from the transcript in that config dir
        self.assertEqual({c['config_dir'] for c in self.calls('claude')}, {str(other)})

    def test_remote_claude_goes_to_the_paired_session_and_its_result_comes_back_through_the_network(self):
        import agent_remote
        jobs = self.jobs(claude={'place': 'remote', 'session': 'https://claude.ai/code/session_01AbCdEfGh1234', 'device': 'Laptop'})
        self.assertEqual(jobs.tools()[0], {'id': 'claude', 'name': 'Claude Code', 'place': 'remote', 'device': 'Laptop'})
        with patch.object(agent_remote, 'watch', lambda jobs: None):  # fetched by hand below
            job = wait(jobs, jobs.start(self.sid, 'claude', 'Reply to Sam with the numbers')['id'], done=('waiting', 'failed'))
            end = time.time() + 10
            while not jobs.get(job['id'])['url'] and time.time() < end:  # waiting from the start; the link once it is sent
                time.sleep(0.05)
            job = jobs.get(job['id'])
        self.assertEqual(job['status'], 'waiting', job)
        self.assertEqual((job['url'], job['app'], job['device']), ('https://claude.ai/code/session_01AbCdEfGh1234', 'Open in Claude', 'Laptop'))
        call = self.calls('claude')[0]
        self.assertEqual(call['args'][-2:], ['--cloud', 'session_01AbCdEfGh1234'])
        ticket = call['prompt'].split('with ticket `', 1)[1].split('`', 1)[0]
        digest = __import__('hashlib').sha256(ticket.encode()).hexdigest()
        self.assertEqual(self.tickets, {digest: job['id']})  # only the hash went to the Network
        self.assertNotIn(ticket, json.dumps(self.sent))
        transcript = (Path(self.tmp.name) / 'agent-jobs' / job['id'] / 'turn-1.jsonl').read_text()
        self.assertNotIn(ticket, transcript)
        self.assertEqual(agent_remote.fetch(jobs), 0)
        self.reported.append({'ticket_hash': digest, 'summary': 'Drafted the reply.', 'draft': 'Hi Sam, here they are.',
                              'diff': '', 'links': ['https://example.com/pr/1']})
        self.assertEqual(agent_remote.fetch(jobs), 1)
        job = jobs.get(job['id'])
        self.assertEqual(job['status'], 'result')
        self.assertIn('Drafted the reply.', job['summary'])
        self.assertEqual(job['links'], ['https://example.com/pr/1'])
        self.assertTrue(job['draft_updated'])
        self.assertEqual(self.revised, [(self.sid, 'Hi Sam, here they are.')])
        self.assertEqual(self.sent[-1], {'action': 'ack', 'receipts': [{'ticket_hash': digest, 'report': 1}]})

    def test_a_cancelled_result_cannot_replace_the_card_draft(self):
        jobs = self.jobs()
        with patch.object(jobs, 'launch', lambda *a, **k: None):
            job = jobs.start(self.sid, 'claude', 'Rewrite draft')
        work = jobs.work(job['id'])
        (work / 'draft.md').write_text('An old result')
        jobs.finish(job['id'], 'cancelled', only_if=agent_jobs.ACTIVE)
        jobs.result(job['id'], 'old-session', 'Finished late', files=[{'path': 'draft.md', 'size': 13}], diff='')
        self.assertEqual(self.revised, [])
        self.assertEqual(jobs.get(job['id'])['status'], 'cancelled')

    def test_an_ack_failure_is_retried_after_recreating_the_jobs(self):
        import agent_remote
        jobs = self.jobs(claude={'place': 'remote', 'session': 'session_01AbCdEfGh1234'})
        with patch.object(agent_remote, 'watch', lambda jobs: None):
            job = wait(jobs, jobs.start(self.sid, 'claude', 'Summarize')['id'], done=('waiting', 'failed'))
            end = time.time() + 10
            while not jobs.get(job['id'])['url'] and time.time() < end:
                time.sleep(.05)
        with jobs.connect() as db:
            digest = db.execute('SELECT token_hash FROM agent_jobs WHERE id=?', (job['id'],)).fetchone()[0]
        self.reported.append({'ticket_hash': digest, 'report': 1, 'summary': 'Ready', 'draft': 'The draft'})
        real = jobs._network
        def fail_ack(body):
            if body['action'] == 'ack':
                raise OSError('Connection lost')
            return real(body)
        jobs._network = fail_ack
        self.assertEqual(agent_remote.fetch(jobs), 1)
        self.assertEqual(jobs.get(job['id'])['status'], 'result')
        restarted = self.jobs()
        self.assertEqual(agent_remote.fetch(restarted), 0)
        self.assertEqual(self.reported, [])
        self.assertEqual(self.revised, [(self.sid, 'The draft')])
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT acked FROM agent_result_receipts').fetchone()[0], 1)

    def test_cancel_or_an_owner_edit_between_checks_cannot_replace_the_draft(self):
        app = object.__new__(a.App)
        app.store, app.config = self.store, self.config
        action = {'kind': 'email', 'to': ['sam@client.example'], 'body': 'Approved original', 'subject': 'Hi', 'revision': 'r1'}
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET status='prepared',next_action=? WHERE id=?", (json.dumps(action), self.sid))
        jobs = self.jobs()
        jobs.revise = app.agent_revise
        with patch.object(jobs, 'launch', lambda *a, **k: None):
            job = jobs.start(self.sid, 'claude', 'Rewrite the draft')
        with jobs.connect() as db:
            claimed = dict(db.execute('SELECT * FROM agent_jobs WHERE id=?', (job['id'],)).fetchone())
        # Cancellation after the result's first check is validated in the same transaction as the edit.
        jobs.finish(job['id'], 'cancelled', only_if=agent_jobs.ACTIVE)
        with self.assertRaisesRegex(ValueError, 'stopped or the draft changed'):
            app.agent_revise(self.sid, 'Stale', claimed)
        with jobs.connect() as db:
            db.execute("UPDATE agent_jobs SET status='running' WHERE id=?", (job['id'],))
        self.store.revise_draft(self.sid, 'r1', 'dashboard', body='Owner revised it')
        with self.assertRaisesRegex(ValueError, 'stopped or the draft changed'):
            app.agent_revise(self.sid, 'Stale', claimed)
        with self.store.connect() as db:
            self.assertEqual(json.loads(db.execute('SELECT next_action FROM suggestions WHERE id=?', (self.sid,)).fetchone()[0])['body'], 'Owner revised it')

    def test_a_replacement_during_ack_wait_updates_the_result_once(self):
        import agent_remote
        jobs = self.jobs(claude={'place': 'remote', 'session': 'session_01AbCdEfGh1234'})
        with patch.object(jobs, 'launch', lambda *a, **k: None):
            job = jobs.start(self.sid, 'claude', 'Summarize')
        digest = 'a' * 64
        with jobs.connect() as db:
            db.execute("UPDATE agent_jobs SET status='waiting',token_hash=? WHERE id=?", (digest, job['id']))
        self.reported.append({'ticket_hash': digest, 'report': 1, 'summary': 'First', 'draft': 'First draft'})
        network = jobs._network
        def replaced(body):
            if body['action'] == 'ack':
                self.reported[:] = [{'ticket_hash': digest, 'report': 2, 'summary': 'Corrected', 'draft': 'Corrected draft'}]
                return {'ok': True, 'acked': []}
            return network(body)
        jobs._network = replaced
        self.assertEqual(agent_remote.fetch(jobs), 1)
        jobs._network = network
        self.assertEqual(agent_remote.fetch(jobs), 1)
        self.assertEqual(jobs.get(job['id'])['summary'], 'Corrected')
        self.assertEqual(self.revised, [(self.sid, 'First draft'), (self.sid, 'Corrected draft')])
        self.assertEqual(agent_remote.fetch(jobs), 0)

    def test_a_previous_turn_cannot_complete_or_fail_the_retry(self):
        jobs = self.jobs()
        with patch.object(jobs, 'launch', lambda *a, **k: None):
            job = jobs.start(self.sid, 'claude', 'Summarize')
            jobs.finish(job['id'], 'cancelled', only_if=agent_jobs.ACTIVE)
            retried = jobs.retry(job['id'])
        self.assertEqual(retried['turns'], 2)
        jobs.result(job['id'], 'stale', 'Old completion', files=[], diff='', turn=1)
        jobs.finish(job['id'], 'failed', error='Old error', turn=1)
        jobs.progress(job['id'], 'Old progress', turn=1)
        jobs.session(job['id'], 'stale-session', turn=1)
        self.assertEqual(jobs.get(job['id'])['status'], 'sent')
        self.assertIsNone(jobs.get(job['id'])['progress'])
        with jobs.connect() as db:
            self.assertIsNone(db.execute('SELECT session_id FROM agent_jobs WHERE id=?', (job['id'],)).fetchone()[0])

    def test_codex_cloud_runs_there_and_its_diff_comes_back(self):
        import agent_remote
        jobs = self.jobs(codex={'place': 'cloud', 'env': 'env_123'})
        with patch.object(agent_remote, 'CODEX_POLL', 0.05):
            job = wait(jobs, jobs.start(self.sid, 'codex', 'Fix the typo in README')['id'])
        self.assertEqual(job['status'], 'result', job)
        self.assertEqual(self.calls('codex')[0]['args'][:4], ['cloud', 'exec', '--env', 'env_123'])
        self.assertEqual([f['path'] for f in job['files']], ['README.md'])
        self.assertIn('+the', jobs.diff(job['id']))
        self.assertEqual((job['url'], job['app']), ('https://chatgpt.com/codex/tasks/task_e_0123456789abcdef0123456789abcdef', 'Open in Codex cloud'))
        self.assertFalse(job['can_follow_up'])

    def test_claude_falls_back_to_print_mode_when_the_folder_is_not_trusted(self):
        with patch.dict(os.environ, {'AJ_UNTRUSTED': '1'}):
            jobs = self.jobs()
            job = wait(jobs, jobs.start(self.sid, 'claude', 'Summarize the card into summary.md')['id'])
        self.assertEqual(job['status'], 'result', job)
        self.assertEqual(job['summary'], 'Wrote summary.md. See https://example.com/x')
        self.assertEqual(self.calls('claude')[1]['args'], ['-p', '--output-format', 'stream-json', '--verbose'])
        self.assertFalse(job.get('url'))
        jobs = self.jobs(claude={'background': False})
        wait(jobs, jobs.follow_up(job['id'], 'More')['id'])
        self.assertEqual(self.calls('claude')[-1]['args'], ['-p', '--output-format', 'stream-json', '--verbose', '--resume', 'sess-1'])

    def test_a_rewritten_draft_goes_back_on_the_card_once(self):
        self.drafts = [{'kind': 'email', 'to': 'sam@client.example', 'subject': 'Numbers', 'body': 'Hi Sam'}]
        jobs = self.jobs()
        self.assertIn('draft.md', jobs.draft(self.sid)['brief'])
        job = wait(jobs, jobs.start(self.sid, 'claude', 'Rewrite the draft')['id'])
        self.assertEqual(self.revised, [(self.sid, 'Hi Sam,\n\nHere are the numbers.')])
        self.assertTrue(job['draft_updated'])
        job = wait(jobs, jobs.follow_up(job['id'], 'Shorter draft')['id'])
        self.assertEqual(len(self.revised), 1)  # unchanged draft.md: the owner's own edits stay
        self.assertFalse(job['draft_updated'])

    def test_codex_runs_in_the_app_server_so_the_thread_shows_in_the_codex_app(self):
        with patch.object(agent_jobs, 'DESKTOP_CODEX', (str(self.bin / 'codex'),)):
            self.assertEqual(agent_jobs.desktop_codex(), str(self.bin / 'codex'))  # the app's own codex when it is there
        jobs = self.jobs()
        job = wait(jobs, jobs.start(self.sid, 'codex', 'Write out.txt, approve this')['id'])
        self.assertEqual(job['status'], 'result', job)
        self.assertEqual(job['summary'], 'Wrote out.txt.')
        self.assertEqual([f['path'] for f in job['files']], ['out.txt'])
        call = self.calls('codex')[0]
        self.assertEqual(call['args'], ['app-server', '--listen', 'stdio://'])
        methods = [c.get('method') for c in call['calls'] if 'method' in c]
        self.assertEqual(methods[:3], ['initialize', 'initialized', 'thread/start'])
        self.assertIn({'method': 'thread/name/set', 'params': {'threadId': THREAD, 'name': 'Sum up the quarterly numbers'}}, call['calls'])  # the card's title
        self.assertIn({'answer': {'decision': 'decline'}}, call['calls'])  # nothing is approved on the owner's behalf
        self.assertEqual(job['url'], 'codex://threads/' + THREAD)
        self.assertEqual(job['app'], 'Open in Codex')
        wait(jobs, jobs.follow_up(job['id'], 'Again')['id'])
        resume = [c for c in self.calls('codex')[1]['calls'] if c.get('method') == 'thread/resume']
        self.assertEqual(resume[0]['params']['threadId'], THREAD)

    def test_a_follow_up_while_the_thread_is_open_in_the_codex_app_goes_on_in_a_copy(self):
        jobs = self.jobs()
        job = wait(jobs, jobs.start(self.sid, 'codex', 'Write out.txt')['id'])
        with patch.dict(os.environ, {'AJ_WRITER': '1'}):
            job = wait(jobs, jobs.follow_up(job['id'], 'Again')['id'])
        self.assertEqual(job['status'], 'result', job)
        self.assertEqual(job['url'], 'codex://threads/' + FORK)  # the card opens the copy now
        calls = self.calls('codex')[1]['calls']
        self.assertIn({'method': 'thread/fork', 'params': {'threadId': THREAD}}, calls)
        self.assertIn({'method': 'thread/name/set', 'params': {'threadId': FORK, 'name': 'Sum up the quarterly numbers'}}, calls)
        self.assertEqual([c['params']['threadId'] for c in calls if c.get('method') == 'turn/start'], [FORK])

    def test_when_it_cannot_go_on_the_card_says_to_carry_on_in_codex(self):
        jobs = self.jobs()
        job = wait(jobs, jobs.start(self.sid, 'codex', 'Write out.txt')['id'])
        with patch.dict(os.environ, {'AJ_WRITER': 'nofork'}):
            job = wait(jobs, jobs.follow_up(job['id'], 'Again')['id'])
        self.assertEqual((job['status'], job['error_kind']), ('failed', 'busy'))
        self.assertIn('open in the Codex app', job['error'])
        self.assertEqual((job['url'], job['app']), ('codex://threads/' + THREAD, 'Open in Codex'))

    def test_codex_falls_back_to_exec_without_the_app_server(self):
        with patch.dict(os.environ, {'AJ_NO_APPSERVER': '1'}):
            jobs = self.jobs()
            job = wait(jobs, jobs.start(self.sid, 'codex', 'Write out.txt')['id'])
            self.assertEqual(job['status'], 'result', job)
            self.assertEqual(self.calls('codex')[0]['args'], ['exec', '--json', '-'])
            jobs = self.jobs(codex={'app_server': False})
            wait(jobs, jobs.follow_up(job['id'], 'Again')['id'])
            self.assertEqual(self.calls('codex')[-1]['args'], ['exec', 'resume', '--json', 'thread-1', '-'])

    def test_one_job_per_card_at_a_time_and_cancel_stops_it(self):
        jobs = self.jobs()
        job = jobs.start(self.sid, 'claude', 'sleep a while')
        with self.assertRaisesRegex(ValueError, 'already on this card'):
            jobs.start(self.sid, 'codex', 'Something else')
        for _ in range(100):
            if self.calls():
                break
            time.sleep(0.05)
        job = jobs.cancel(job['id'])
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(wait(jobs, job['id'])['status'], 'cancelled')
        with self.assertRaises(ValueError):
            jobs.cancel(job['id'])

    def test_a_failure_shows_and_can_be_tried_again(self):
        jobs = self.jobs()
        job = wait(jobs, jobs.start(self.sid, 'claude', 'this will fail')['id'])
        self.assertEqual((job['status'], job['error']), ('failed', 'It broke'))
        with patch.object(jobs, 'launch') as launch:
            jobs.retry(job['id'])
        # It had a session: Try again carries on there, and starts over from the brief only if that is gone.
        launch.assert_called_once_with(job['id'], agent_jobs.CARRY_ON, resume=job_session(jobs, job['id']), fresh='this will fail')

    def test_try_again_after_a_restart_carries_on_in_the_same_codex_thread(self):
        jobs = self.jobs()
        job = wait(jobs, jobs.start(self.sid, 'codex', 'Write out.txt')['id'])
        with self.store.connect() as db:  # as recover() leaves a job that was running when The Machine restarted
            db.execute("UPDATE agent_jobs SET status='running' WHERE id=?", (job['id'],))
        jobs.recover()
        job = jobs.get(job['id'])
        self.assertEqual((job['status'], job['error_kind'], job['error']), ('failed', 'restart', agent_jobs.RESTARTED))
        job = wait(jobs, jobs.retry(job['id'])['id'])
        self.assertEqual(job['status'], 'result', job)
        calls = self.calls('codex')[-1]['calls']
        self.assertIn({'method': 'thread/resume', 'params': {'threadId': THREAD}}, calls)
        self.assertNotIn('thread/start', [c.get('method') for c in calls])
        self.assertEqual([c['params']['input'][0]['text'] for c in calls if c.get('method') == 'turn/start'], [agent_jobs.CARRY_ON])
        self.assertEqual(job['url'], 'codex://threads/' + THREAD)

    def test_try_again_starts_over_from_the_brief_when_the_thread_is_gone(self):
        jobs = self.jobs()
        job = wait(jobs, jobs.start(self.sid, 'codex', 'Write out.txt')['id'])
        jobs.finish(job['id'], 'failed', error=agent_jobs.RESTARTED, error_kind='restart')
        with patch.dict(os.environ, {'AJ_GONE': '1'}):
            job = wait(jobs, jobs.retry(job['id'])['id'])
        self.assertEqual(job['status'], 'result', job)
        calls = self.calls('codex')[-1]['calls']
        self.assertEqual([c.get('method') for c in calls if 'method' in c][:3], ['initialize', 'initialized', 'thread/start'])
        self.assertEqual([c['params']['input'][0]['text'] for c in calls if c.get('method') == 'turn/start'], ['Write out.txt'])

    def test_the_codex_link_opens_the_thread_on_this_computer_from_other_devices(self):
        jobs = self.jobs()
        job = wait(jobs, jobs.start(self.sid, 'codex', 'Write out.txt')['id'])
        self.assertIsNone(job['url_elsewhere'])  # the Codex app here has no remote connections
        codex = Path(os.environ['HOME']) / '.codex'
        codex.mkdir()
        import sqlite3
        with sqlite3.connect(codex / 'state_5.sqlite') as db:
            db.execute('CREATE TABLE remote_control_enrollments(websocket_url TEXT, account_id TEXT, app_server_client_name TEXT, server_id TEXT, '
                       'environment_id TEXT, server_name TEXT, updated_at INTEGER, remote_control_enabled INTEGER)')
            db.execute("INSERT INTO remote_control_enrollments VALUES('wss://x', 'acct', 'Codex Desktop', 'srv', 'env_0123abcd', 'desk', 1, 1)")
        agent_jobs._HOST.update(at=0.0, found=None)
        job = jobs.get(job['id'])
        self.assertEqual(job['url'], 'codex://threads/' + THREAD)  # on this computer itself
        self.assertEqual(job['url_elsewhere'], 'codex://threads/' + THREAD + '?hostId=remote-control%3Aenv_0123abcd')

    def test_a_job_that_takes_too_long_stops(self):
        jobs = self.jobs()
        jobs.timeout = 1
        job = wait(jobs, jobs.start(self.sid, 'claude', 'sleep now')['id'])
        self.assertEqual((job['status'], job['error_kind']), ('failed', 'timeout'))

    def test_codex_runs_in_a_unit_of_its_own_so_a_restart_of_the_machine_does_not_stop_it(self):
        def token_hash():
            run = next(c for c in self.calls('systemd-run'))
            return run['args'][run['args'].index('--ws-token-sha256') + 1]
        server = FakeWsAppServer(token_hash)
        self.addCleanup(server.close)
        with patch.dict(os.environ, {'AJ_WS_PORT': str(server.port)}), patch.object(agent_jobs, 'can_detach', lambda: True), \
                patch.object(agent_jobs, 'own_unit', lambda: 'personal-assistant.service'):
            jobs = self.jobs()
            job = jobs.start(self.sid, 'codex', 'Write out.txt')
            self.assertTrue(server.started.wait(20))
            run = self.calls('systemd-run')[0]['args']
            # The service's own hardening and environment carry over; values never go on the command line.
            for prop in ('NoNewPrivileges=yes', 'PrivateTmp=yes', 'UMask=0077'):
                self.assertIn('--property=' + prop, run)
            self.assertIn('--setenv=PATH', run)
            self.assertFalse([a for a in run if a.startswith('--setenv=') and '=' in a[len('--setenv='):]])
            self.assertFalse([a for a in run if a.startswith('--setenv=INVOCATION_ID')])
            self.assertEqual(run[run.index('--') + 2:run.index('--') + 7], ['app-server', '--listen', 'ws://127.0.0.1:0', '--ws-auth', 'capability-token'])
            folder = Path(self.tmp.name) / 'agent-jobs' / job['id']
            live = json.loads((folder / 'app-server.json').read_text())
            self.assertNotIn(live['token'], json.dumps(run))  # only its hash
            self.assertEqual(stat.S_IMODE((folder / 'app-server.json').stat().st_mode), 0o600)
            with self.assertRaises(OSError):
                agent_jobs.AppSocket(server.port, 'not-the-token')
            # The Machine restarts while the turn runs on in its unit: the new service follows it to its end.
            again = self.jobs()
            again.recover()
            job = wait(again, job['id'])
        self.assertEqual((job['status'], job['summary']), ('result', 'Done after the restart.'), job)
        self.assertIn({'method': 'thread/resume', 'params': {'threadId': THREAD}}, server.calls)
        self.assertEqual([c['method'] for c in server.calls].count('turn/start'), 1)  # not started over
        self.assertIn(['--user', 'stop', '--no-block', live['unit']], [c['args'] for c in self.calls('systemctl')])

    def test_a_restart_fails_running_jobs_and_restarts_waiting_ones(self):
        jobs = self.jobs()
        with patch.object(jobs, 'launch'):
            job = jobs.start(self.sid, 'claude', 'Summarize')
        with self.store.connect() as db:
            db.execute("UPDATE agent_jobs SET status='running' WHERE id=?", (job['id'],))
        with patch.object(jobs, 'launch') as launch:
            jobs.recover()
        self.assertEqual(jobs.get(job['id'])['error_kind'], 'restart')
        self.assertEqual(jobs.get(job['id'])['error'], agent_jobs.RESTARTED)  # says why, and what Try again does
        launch.assert_not_called()

    def test_the_brief_and_result_are_sealed_at_rest(self):
        jobs = self.jobs()
        job = wait(jobs, jobs.start(self.sid, 'claude', 'Summarize the card into summary.md')['id'])
        import sqlite3
        raw = sqlite3.connect(self.config['database']).execute('SELECT brief,summary,diff FROM agent_jobs WHERE id=?', (job['id'],)).fetchone()
        self.assertTrue(all(isinstance(v, bytes) for v in raw), raw)

    def test_dashboard_only_and_marking_the_card_done_closes_the_job(self):
        app = object.__new__(a.App)
        app.store, app.config = self.store, {'accounts': []}
        app.agents = self.jobs()
        with self.assertRaisesRegex(ValueError, 'from the dashboard'):
            app.action('agent_send', {'suggestion_id': self.sid, 'tool': 'claude', 'brief': 'x'})
        job = wait(app.agents, app.action('agent_send', {'suggestion_id': self.sid, 'tool': 'claude', 'brief': 'Summarize'}, owner_dashboard=True)['id'])
        status = {'suggestions': [{'id': self.sid}]}
        with self.store.connect() as db:
            app.agents.annotate(db, status['suggestions'])
        self.assertEqual(status['suggestions'][0]['agent_job']['status'], 'result')
        app.store.decide(self.sid, 'complete', '', 'd1', 'dashboard')
        app.agents.card_closed(self.sid)
        self.assertEqual(app.agents.get(job['id'])['outcome'], 'done')
        self.assertIn('agent_send', a.OWNER_ACTIONS)



class AgentSettings(unittest.TestCase):
    def test_a_hosted_owner_pairs_a_claude_session_and_a_codex_environment(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'config.json'
        path.write_text(json.dumps({'database': 'x', 'accounts': [], 'manage_url': 'https://app.example.test/machine', 'other': 1}, indent=2) + '\n')
        app = object.__new__(a.App)
        app.config_path, app.config = path, json.loads(path.read_text())
        self.assertIn('set_agents', a.OWNER_ACTIONS)
        view = app.agents_view()
        self.assertEqual((view['claude_session'], view['connector'], view['saveable']), ('', 'https://app.example.test/mcp', True))
        with self.assertRaises(ValueError):
            app.set_agents({'claude_session': 'not a session'})
        saved = app.set_agents({'claude_session': 'https://claude.ai/code/session_01AbCdEfGh1234', 'device': 'Laptop', 'codex_env': 'env_123'})
        self.assertEqual((saved['claude_session'], saved['device'], saved['codex_env']), ('session_01AbCdEfGh1234', 'Laptop', 'env_123'))
        written = json.loads(path.read_text())
        self.assertEqual(written['agents'], {'claude': {'place': 'remote', 'session': 'session_01AbCdEfGh1234', 'device': 'Laptop'},
                                             'codex': {'place': 'cloud', 'env': 'env_123'}})
        self.assertEqual(written['other'], 1)
        app.set_agents({'codex_env': 'env_123'})
        self.assertEqual(json.loads(path.read_text())['agents']['claude'], {})
        # A Machine on the owner's own computer with nothing remote set: no such settings (its local CLIs are used)
        app.config = {'accounts': []}
        self.assertIsNone(app.agents_view())


class TaskFirstBrief(unittest.TestCase):
    """The job a card hands to Claude Code or Codex leads with the card's next step (its Needs you line), not its latest
    chat reply; the owner's note follows word for word; the source email's full text is in the job folder."""
    NEEDS = 'add the agreed Listly link to your agency lead-generation article'
    REPLY = 'Yes, you still need to add the link: their last email confirms position 6 once the link is live.'
    NOTE = 'Do I still need to add the link?'
    MAIL = ('Hi,\n\nHere is the final entry for the list. We are happy to take position 6.\n\n'
            'Link to use: https://listly.example/agencies\n\nThanks, Robin')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = a.Store(root / 'events.sqlite', protect=True)
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'm1', 'kind': 'gmail.message', 'occurred_at': '2026-10-22T09:00:00+00:00',
                                             'title': 'Final entry for the agency list', 'body': self.MAIL, 'deleted': False,
                                             'metadata': {'from': 'Robin Vendor <robin@listly.example>', 'to': 'me@example.com', 'thread_id': 't1',
                                                          'labels': ['INBOX'], 'source_url': 'https://mail.google.com/mail/u/0/#all/t1'}}])
        with self.store.connect() as db:
            event_id = db.execute('SELECT id FROM events').fetchone()[0]
        self.sid = self.store.suggest('listly', 'Listly sent the final entry and offers position 6', 'Robin sent the final entry.', [event_id])['id']
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET status='prepared',body=?,outcome=? WHERE id=?",
                       (self.REPLY, json.dumps({'state': 'needs_you', 'needs': self.NEEDS}), self.sid))
            db.execute("INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES('d1',?,'other',?,'dashboard',?)",
                       (self.sid, self.NOTE, '2026-10-22T10:00:00+00:00'))
            db.execute("INSERT INTO preparation_runs(job_id,suggestion_id,session_job_id,status,started_at) VALUES('job1',?,'job1','ok',?)",
                       (self.sid, '2026-10-22T10:01:00+00:00'))
            db.execute('INSERT INTO preparation_calls(job_id,call_id,tool,params,started_at) VALUES(?,?,?,?,?)',
                       ('job1', 'c1', 'assistant_signals', json.dumps({'action': 'prepare', 'args': {'suggestion_id': self.sid, 'body': self.REPLY}}),
                        '2026-10-22T10:01:30+00:00'))
        self.app = object.__new__(a.App)
        self.app.store = self.store
        self.app.config = {'accounts': [], 'database': str(root / 'events.sqlite')}
        self.config = {'database': str(root / 'events.sqlite'), 'accounts': [], 'agents': {}}

    def jobs(self):
        return agent_jobs.AgentJobs(self.store.connect, self.config, Path(self.tmp.name) / 'agent-jobs', card=self.app.agent_card)

    def test_the_task_comes_first_then_the_note_then_background(self):
        brief = self.jobs().draft(self.sid)['brief']
        lines = brief.splitlines()
        self.assertEqual(lines[0], 'Task: Add the agreed Listly link to your agency lead-generation article.')
        self.assertEqual(lines[2:4], ['My note on the card, as I wrote it:', self.NOTE])
        self.assertNotIn('What this is about', brief)
        # The latest chat reply is background, labelled so, after the task; never the lead.
        self.assertIn('So far (background only; the task above is what to do):', brief)
        self.assertIn('- The Machine answered: ' + self.REPLY, brief)
        self.assertGreater(brief.index(self.REPLY), brief.index('Task: '))
        self.assertEqual(brief.count(self.NOTE), 1)  # the note isn't repeated as a chat turn
        self.assertIn('- With: Robin Vendor <robin@listly.example> (email)', brief)
        self.assertIn('card/messages.md', brief)
        # Raw links come after the files, as a second reference only.
        self.assertGreater(brief.index('https://mail.google.com/'), brief.index('card/messages.md'))
        self.assertIn('Done looks like: the task above done', brief)
        self.assertTrue(lines[-1].startswith('- Leave anything that sends, publishes, pays or submits for me'))

    def test_the_folder_has_the_source_email_text(self):
        parts = self.app.agent_card(self.sid)
        jobs = self.jobs()
        jobs.prepare_folder('aj_0123456789abcdef', jobs.draft(self.sid)['brief'], parts)
        work = Path(self.tmp.name) / 'agent-jobs' / 'aj_0123456789abcdef' / 'work'
        text = (work / 'card/messages.md').read_text()
        self.assertIn(self.MAIL.strip(), text)  # the whole body, not a snippet
        self.assertIn('From: Robin Vendor <robin@listly.example>', text)
        self.assertIn('To: me@example.com', text)
        self.assertTrue((work / 'BRIEF.md').read_text().startswith('Task: Add the agreed Listly link'))

    def test_a_job_that_runs_elsewhere_carries_the_messages_newest_first(self):
        # Remote Claude Code and Codex cloud don't get the job folder: the source text travels in the job, under a cap.
        later = 'Robin again. ' + 'The banner size is 600 by 300. ' * 400
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'm2', 'kind': 'gmail.message', 'occurred_at': '2026-10-22T11:00:00+00:00',
                                             'title': 'One more thing', 'body': later, 'deleted': False,
                                             'metadata': {'from': 'Robin Vendor <robin@listly.example>', 'thread_id': 't1', 'labels': ['INBOX']}}])
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM events ORDER BY id')]
            db.execute('UPDATE suggestions SET event_ids=? WHERE id=?', (json.dumps(ids), self.sid))
        jobs = self.jobs()
        with patch.object(agent_jobs.AgentJobs, 'tools', lambda self: [{'id': 'codex', 'name': 'Codex', 'place': 'cloud'}]):
            brief = jobs.draft(self.sid)['brief']
        self.assertTrue(brief.startswith('Task: Add the agreed Listly link'))
        self.assertIn('The source messages, newest first, with their text', brief)
        self.assertNotIn('card/messages.md', brief)  # not a file it can read there
        self.assertLess(brief.index('### One more thing'), brief.index('### Final entry for the agency list'))
        self.assertIn('Robin again.', brief)
        self.assertRegex(brief, r'\(Cut here: \d+ more characters in the original\.\)')
        self.assertLessEqual(len(brief), agent_jobs.BRIEF_MAX)
        self.assertTrue(brief.splitlines()[-1].startswith('- Leave anything that sends, publishes, pays or submits for me'))
        # On this computer the folder has them, so the job only points there.
        with patch.object(agent_jobs.AgentJobs, 'tools', lambda self: [{'id': 'codex', 'name': 'Codex', 'place': 'local'}]):
            local = jobs.draft(self.sid)['brief']
        self.assertIn('card/messages.md', local)
        self.assertNotIn('Robin again.', local)
        # Many messages: the older ones that don't fit are named as left out.
        many = [{'title': f'Mail {n}', 'from': 'x@example.com', 'occurred_at': f'2026-10-{10 + n}', 'body': 'word ' * 600} for n in range(9)]
        text = agent_jobs.inline_messages(many, 7000)
        self.assertTrue(text.startswith('### Mail 8'))
        self.assertRegex(text, r'\(\d+ older messages left out here, to keep this short\.\)$')
        self.assertLessEqual(len(text), 7000)

    def test_the_task_follows_the_card(self):
        row = {'status': 'prepared', 'title': 'Pick a venue', 'outcome': json.dumps({'state': 'needs_you', 'needs': 'which venue do we book?'})}
        self.assertEqual(a.card_task(row), 'Help me answer this: Which venue do we book?')
        row['outcome'] = json.dumps({'state': 'your_step', 'step': 'submit'})
        self.assertEqual(a.card_task(row), 'Check that everything is ready for me to submit, and fix what isn’t.')
        self.assertEqual(a.card_task({'status': 'pending', 'title': 'Sum up the quarterly numbers'}), 'Sum up the quarterly numbers.')
        blocked = {'status': 'accepted', 'title': 'X', 'preparation_status': 'blocked', 'preparation_reason': 'Which account should pay?'}
        self.assertEqual(a.card_task(blocked), 'Help me answer this: Which account should pay?')

if __name__ == '__main__':
    unittest.main()
