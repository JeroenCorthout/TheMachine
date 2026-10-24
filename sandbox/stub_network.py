"""A stand-in for The Machine Network, for the sandbox: the same endpoints the app calls (/directory, /inbox, /ask,
/answer, /decide, /claim(s), /friends, /me), answered from memory. Teammates and friends are the fixture's invented
people; their Machines answer a question a few seconds after it is asked, so asking, handing off and adding friends
behave as they would. Each of their Machines has its own keys, as on the real Network: what they say to this Machine is
sealed for it, and what it says to them comes sealed. Nothing leaves this machine."""
import datetime as dt
import json
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import network_crypto as nc  # noqa: E402
import stub_graph  # noqa: E402

ANSWER_AFTER = 3  # seconds before a teammate's Machine answers


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())


class Refused(Exception):
    """What the Network says no to (426), as the real one does to anything not sealed."""


class Network:
    def __init__(self, fixture, groups, token):
        self.net, self.groups, self.token = fixture['network'], set(groups), token
        self.lock = threading.Condition()
        self.inbox = {'asks': [], 'answers': [], 'notices': []}
        self.answered = []  # what this Machine answered teammates, for tests
        self.friends = [dict(f) for f in self.net['friends']['friends']]
        self.requests_in = [{'id': 'frq_in_0001', **self.net['friends']['request_in'], 'at': now()}]
        self.requests_out = [{'id': 'frq_out_0001', 'username': self.net['friends']['request_out'], 'at': now()}]
        self.blocked = []
        self.keys = None  # this Machine's public keys, as it published them (POST /keys)
        self.peers = {}  # each simulated teammate's and friend's Machine: its private keys
        self.sent = {}  # what this Machine asked, by question id: (kind, member id), to seal the answer for it
        self.notices = {n['id']: {**n, 'at': now()} for n in self.net['notices']}
        self.trust = []  # signed trust statements (fleet_trust.py), as Machines post them
        self.graph = []  # the Shared Graph blobs waiting for this Machine (stub_graph.py)
        self.hold = {h['to'] for h in self.net.get('handed', [])}  # teammates whose Machine keeps a handoff waiting for them

    # -- sealing, as the simulated Machines do
    def peer(self, member_id):
        if member_id not in self.peers:
            self.peers[member_id] = nc.new_keys()
        return self.peers[member_id]

    def sealed_for_me(self, body, purpose, kind, member_id, ref):
        return nc.seal(body, self.peer(member_id), self.keys, {'p': purpose, 'k': kind, 'f': member_id, 't': self.net['owner']['member_id'], 'r': ref})

    def handed_out(self, found):
        """What waits, sealed for this Machine; held back until it has published its keys."""
        if not self.keys:
            return {'asks': [], 'answers': [], 'notices': found['notices']}, {**found, 'notices': []}
        asks = [{**{k: v for k, v in a.items() if k != 'question'}, 'question': {},
                 'sealed': self.sealed_for_me(a['question'], 'ask', a['kind'], a['from'], a.get('request_id'))} for a in found['asks']]
        answers = [{**{k: v for k, v in a.items() if k != 'answer'},
                    **({'answer': None, 'sealed': self.sealed_for_me(a['answer'], 'answer', a['kind'], a['from'], a['id'])} if a.get('answer') is not None else {})}
                   for a in found['answers']]
        return {'asks': asks, 'answers': answers, 'notices': found['notices']}, {'asks': [], 'answers': [], 'notices': []}

    def opened(self, body):
        """An answer this Machine sealed for a simulated teammate, opened as their Machine would."""
        asked = self.sent.get(body.get('id'))
        if body.get('sealed') is None or not asked or not self.keys:
            return body
        kind, member_id = asked
        answer = nc.open_sealed(body['sealed'], self.peer(member_id), self.keys,
                                {'p': 'answer', 'k': kind, 'f': self.net['owner']['member_id'], 't': member_id, 'r': body['id']})
        return {**{k: v for k, v in body.items() if k != 'sealed'}, 'answer': answer}

    # -- what the app reads
    def me(self, emails=None):
        owner = self.net['owner']
        return {'member_id': owner['member_id'], 'name': owner['name'], 'role': 'owner', 'emails': [owner['email']], 'machine': True,
                'account_email': owner['email'], 'label': owner['name'], 'me': True, **({'keys': self.keys} if self.keys else {})}

    def member(self, m):
        return {'member_id': m['member_id'], 'name': m['name'], 'role': m.get('role', 'member'), 'emails': [m['email']] if m.get('email') else [],
                'machine': m.get('machine', True), 'account_email': m.get('email', ''), 'label': m['name'],
                **({'keys': nc.public_keys(self.peer(m['member_id']))} if m.get('machine', True) else {'answers_with': 'no Machine yet'})}

    def directory(self):
        fleets = []
        for kind in ('work', 'family'):
            if kind in self.groups:
                group = self.net[kind]
                fleets.append({'id': group['id'], 'kind': kind, 'name': group['name'], 'asks': ['relationships', 'meeting', 'brief', 'anything',
                               'intro', 'handoff', 'followup', 'skill'] if kind == 'work' else ['availability', 'todo', 'event', 'skill'], 'me': {'member_id': self.net['owner']['member_id'], 'role': 'owner'},
                               'members': [self.me()] + [self.member(m) for m in group['team']],
                               **({'trust': list(self.trust)} if kind == 'work' else {})})
        if 'friends' in self.groups:
            fleets.append({'id': self.net['friends']['id'], 'kind': 'friends', 'name': 'Friends', 'username': self.net['owner']['username'],
                           'asks': ['availability', 'skill'], 'me': {'member_id': self.net['owner']['member_id'], 'role': 'owner'},
                           'members': [self.me()] + [{'member_id': f['member_id'], 'name': f['name'], 'role': 'friend', 'emails': [], 'machine': True,
                                                      'label': f['name'] + ' (@' + f['username'] + ')', 'keys': nc.public_keys(self.peer(f['member_id']))}
                                                     for f in self.friends],
                           'requests': {'in': list(self.requests_in), 'out': list(self.requests_out)}, 'blocked': list(self.blocked)})
        return {'fleets': fleets}

    def group_of(self, fleet_id):
        for kind in ('work', 'family'):
            if kind in self.groups and fleet_id in (self.net[kind]['id'], kind):
                return self.net[kind]['team']
        if 'friends' in self.groups and fleet_id in (self.net['friends']['id'], 'friends'):
            return [{'member_id': f['member_id'], 'name': f['name']} for f in self.friends]
        return []

    # -- simulated teammates
    def reply(self, kind, index):
        if kind == 'relationships':
            return {'knows': True, 'people': 2, 'closeness': 'close', 'last_contact': 'last week'} if index == 0 else {'knows': False}
        if kind == 'anything':
            return {'text': ['Not that I know of. Tomasz met them in September, I think.', 'Yes, I spoke to their ops lead on Monday.'][index % 2]}
        if kind in ('intro', 'handoff', 'followup'):
            return {'stage': 'accepted'}
        if kind in ('todo', 'event'):
            return {'stage': 'accepted'} if index == 0 else None
        if kind == 'availability':  # the first one busy most mornings, from tomorrow
            day = dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)
            return {'busy': [[f'{day + dt.timedelta(days=n)}T07:00Z', f'{day + dt.timedelta(days=n)}T10:30Z'] for n in range(7)] if index == 0 else []}
        return None

    def answer_later(self, ids, kind, fleet_id):
        def later():
            time.sleep(ANSWER_AFTER)
            with self.lock:
                for index, (member_id, ask_id) in enumerate(ids.items()):
                    if kind == 'handoff' and member_id in self.hold:  # it waits for them, as a real teammate's often does
                        self.inbox['answers'].append({'id': ask_id, 'fleet_id': fleet_id, 'from': member_id, 'kind': kind, 'status': 'waiting',
                                                      'receipt': secrets.token_hex(8)})
                        continue
                    answer = self.reply(kind, index)
                    self.inbox['answers'].append({'id': ask_id, 'fleet_id': fleet_id, 'from': member_id, 'kind': kind,
                                                  'status': 'answered' if answer is not None else 'declined',
                                                  **({'answer': answer} if answer is not None else {}), 'receipt': secrets.token_hex(8)})
                self.lock.notify_all()
        threading.Thread(target=later, daemon=True).start()

    def queue(self, asks=(), notices=()):
        with self.lock:
            self.inbox['asks'] += list(asks)
            self.inbox['notices'] += list(notices)
            self.lock.notify_all()

    # -- the endpoints
    def handle(self, method, path, query, body):
        if method == 'GET' and path == '/directory':
            return self.directory()
        if method == 'POST' and path == '/me':
            return {}
        if method == 'POST' and path == '/keys':  # from now on what waits is sealed for this Machine and handed out
            with self.lock:
                self.keys = body
                self.lock.notify_all()
            return {}
        if method == 'GET' and path == '/inbox':
            wait = min(int((query.get('wait') or ['0'])[0] or 0), 10)
            with self.lock:
                if not (self.keys and (self.inbox['asks'] or self.inbox['answers'])) and not self.inbox['notices']:
                    self.lock.wait(wait)
                found, self.inbox = self.handed_out(self.inbox)
            return {**found, 'ack': True, 'graph': len(self.graph)}
        if method == 'POST' and path in ('/ack', '/answer'):
            if path == '/answer':
                self.answered.append(self.opened(body))
            return {}
        if method == 'POST' and path == '/ask':  # sealed for each Machine, as the Network takes them
            fleet = body.get('fleet_id') or body.get('fleet') or 'work'
            sealed = body.get('sealed') or {}
            if body.get('question') is not None or not sealed or not set(body.get('to') or []) <= set(sealed):
                raise Refused('their Machine needs an update before it can get questions on The Machine Network')
            team = [m for m in self.group_of(fleet) if m['member_id'] in sealed]
            ids = {m['member_id']: 'fa_' + secrets.token_hex(8) for m in team}
            for member_id, ask_id in ids.items():
                self.sent[ask_id] = (body.get('kind'), member_id)
            group = next((g['id'] for g in self.directory()['fleets'] if fleet in (g['id'], g['kind'])), fleet)
            self.answer_later(ids, body.get('kind'), group)
            return {'ids': ids}
        if method == 'POST' and path == '/decide':
            notice = self.notices.get(body.get('id'))
            if not notice:
                return {'status': 'stale', 'reason': 'Already settled'}
            status = 'confirmed' if body.get('choice') == 'confirm' else 'declined'
            notice.update(status=status, version=notice['version'] + 1, buttons=[], outcome='Confirmed' if status == 'confirmed' else 'Declined')
            return {'status': status, 'notice': notice}
        if method == 'POST' and path == '/trust':
            self.trust.append(body['item'])
            return {'ok': True}
        if method == 'POST' and path == '/graph':  # put, relay, ack: the teammates' Machines take its graph key and publish theirs
            blob = body.get('blob') or {}
            if body.get('op') == 'put' and blob.get('header', {}).get('kind') == 'key' and not self.graph and self.keys:
                key = stub_graph.open_key(self, blob, self.net['owner']['member_id'])
                if key:
                    self.graph = stub_graph.blobs(self, body['fleet'], self.net['owner']['member_id'], key)
            return {'ok': True}
        if method == 'GET' and path == '/graph':
            after = int((query.get('after') or ['0'])[0] or 0)
            items = [{**b, 'cursor': n} for n, b in enumerate(self.graph, 1) if n > after]
            return {'items': items, 'cursor': len(self.graph), 'more': False}
        if method == 'POST' and path == '/delivery':
            return {'delivery': {i: {'delivered': True} for i in body.get('ids', [])}}
        if method == 'POST' and path == '/claims':
            return {'claims': {}}
        if method == 'POST' and path == '/claim':
            return {'mine': not body.get('release')}
        if method == 'POST' and path == '/friends':
            return self.friend(body)
        return None

    def friend(self, body):
        action = body.get('action')
        if action == 'add':
            self.requests_out.append({'id': 'frq_' + secrets.token_hex(6), 'username': body.get('username', ''), 'at': now()})
        elif action in ('accept', 'decline', 'block') and body.get('id'):
            found = next((r for r in self.requests_in if r['id'] == body['id']), None)
            self.requests_in = [r for r in self.requests_in if r['id'] != body['id']]
            if found and action == 'accept':
                self.friends.append({'member_id': 'm_fr_' + secrets.token_hex(4), 'name': found['name'], 'username': found['username']})
            if found and action == 'block':
                self.blocked.append({'member_id': 'm_fr_' + secrets.token_hex(4), 'name': found['name']})
        elif action == 'withdraw':
            self.requests_out = [r for r in self.requests_out if r['id'] != body.get('id')]
        elif action in ('remove', 'block') and body.get('member_id'):
            gone = next((f for f in self.friends if f['member_id'] == body['member_id']), None)
            self.friends = [f for f in self.friends if f['member_id'] != body['member_id']]
            if gone and action == 'block':
                self.blocked.append({'member_id': gone['member_id'], 'name': gone['name']})
        elif action == 'unblock':
            self.blocked = [b for b in self.blocked if b['member_id'] != body.get('member_id')]
        return {'ok': True}


def serve(network, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self):
            url = urllib.parse.urlsplit(self.path)
            path = url.path.removeprefix('/api/fleet')
            if self.headers.get('Authorization') != 'Bearer ' + network.token:
                return self.send(403, {'error': 'Not a Machine in this sandbox'})
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length) or b'{}') if length else {}
            try:
                found = network.handle(self.command, path, urllib.parse.parse_qs(url.query), body)
            except Refused as error:
                return self.send(426, {'error': str(error)})
            if found is None:
                print(f'sandbox network: no stand-in for {self.command} {path}', file=sys.stderr, flush=True)
                return self.send(404, {'error': 'Not here'})
            self.send(200, found)

        def send(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = respond

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
