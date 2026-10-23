"""Task pages: a page someone outside the Network can open from the owner's email, see the task's status on, and answer
without an account (docs/network.md). Opt-in per task, and only what the owner approved goes on it.

A handoff by email (Assistant.handoff_email) leaves an ordinary draft on the card and offers a task page. The owner
taps Add a task page, sees exactly what the page shows (the task, the details, the due date, their first name), may
leave parts out and pick how long the link works, and adds it to the email: then this Machine seals the page with a
fresh key (task_invites.seal), the Network keeps the opaque blob, and the draft gets one plain line, "Status and
replies: <link>". The email still goes out only with the owner's own Send.

Answers come back sealed (task_invites.open_answer) in the Network round: shown quoted on the card, told to the owner,
and added to the page's updates, which this Machine seals again as a newer version. Mark done closes the page (it then
ends within 7 days); Stop sharing deletes it from the Network at once and drops the key here.

Never on a page: the card's email or thread, its evidence, the graph, other cards, the recipient's address. The page is
built only from the approved task's fixed fields (task_invites.page), and details that repeat the original email
are refused (task_invites.quotes). The record here (with the link's key) is sealed at rest with the message key."""
from __future__ import annotations

import datetime as dt
import json
import logging
import uuid

import next_actions
import task_invites

LOG = logging.getLogger('personal-assistant')
SCHEMA = 'CREATE TABLE IF NOT EXISTS task_pages(card_id TEXT PRIMARY KEY, state TEXT NOT NULL, record TEXT NOT NULL, updated_at TEXT NOT NULL)'
DAYS = (7, 14, 30)
DEFAULT_DAYS = 14
LIVE = ('shared', 'done')
ACTIONS = ('task_page_share', 'task_page_dismiss', 'task_page_stop')  # Mark done on the card closes the page (card_closed)
TOLD = {'on': "they're on it", 'done': 'they marked it done', 'cant': "they can't do this"}
STATUS_AFTER = {'on': 'in_progress', 'done': 'in_progress', 'cant': 'back'}  # their Done waits for the owner's own


def today():
    return dt.date.today().isoformat()


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


LEAD = {'on': 'is on it', 'done': 'marked it done', 'cant': "can't do this"}


def lead(to, answers, state):
    """The card's first line once the email went out: where it stands with them, their latest answer quoted."""
    who = to or 'Your contact'
    if answers:
        last = answers[-1]
        return f'{who} {LEAD[last["answer"]]}' + (f': \u201c{last["note"]}\u201d' if last['note'] else '.')
    if state == 'shared':
        return f'Waiting for {who} on the task page.'
    return f'Handed to {who} by email.'


class TaskPages:
    def __init__(self, app):
        self.app = app

    # -- storage (the record is sealed at rest: message_seal.DERIVED)
    def load(self, card_id):
        with self.app.store.connect() as db:
            db.execute(SCHEMA)
            row = db.execute('SELECT state,record FROM task_pages WHERE card_id=?', (str(card_id),)).fetchone()
        return (row['state'], json.loads(row['record'])) if row else (None, None)

    def save(self, card_id, state, record):
        with self.app.store.connect() as db:
            db.execute(SCHEMA)
            db.execute('INSERT INTO task_pages(card_id,state,record,updated_at) VALUES(?,?,?,?) ON CONFLICT(card_id) DO UPDATE SET '
                       'state=excluded.state,record=excluded.record,updated_at=excluded.updated_at',
                       (str(card_id), state, json.dumps(record, ensure_ascii=False), stamp()))

    def relay(self):
        network = getattr(self.app, 'fleet', None)
        if not network:
            raise ValueError('Task pages need The Machine Network. Turn it on in Settings.')
        return network.relay

    # -- the offer, made with a handoff by email
    def offer(self, card_id, task):
        """A handoff by email offers a task page on its card. Nothing is made or sent until the owner approves it."""
        state, _ = self.load(card_id)
        if state in LIVE:
            return
        self.save(card_id, 'offered', {'task': task_invites.clean(task)})

    def dismiss(self, card_id):
        state, record = self.load(card_id)
        if state != 'offered':
            raise ValueError('There is no task page to skip on this card.')
        self.save(card_id, 'dismissed', {'task': {'title': record['task']['title']}})
        return {'state': 'dismissed'}

    # -- approving it
    def draft_action(self, card_id):
        """The card's email draft, while it is still a draft: (revision, action)."""
        with self.app.store.connect() as db:
            row = db.execute('SELECT status,next_action FROM suggestions WHERE id=?', (str(card_id),)).fetchone()
        if not row or row['status'] != 'prepared' or not row['next_action']:
            return None, None
        current = json.loads(row['next_action'])
        found = [a for a in (current.get('actions') if current.get('kind') == 'menu' else [current]) if a.get('kind') == 'email']
        return (found[0].get('revision'), found[0]) if found else (None, None)

    def sources(self, card_id):
        """The card's original messages, only to check the page doesn't repeat them."""
        with self.app.store.connect() as db:
            row = db.execute('SELECT event_ids FROM suggestions WHERE id=?', (str(card_id),)).fetchone()
            ids = json.loads(row['event_ids'] or '[]') if row else []
            return [r['body'] for r in db.execute('SELECT body FROM events WHERE id IN (SELECT value FROM json_each(?))', (json.dumps(ids),))
                    if r['body']]

    def share(self, card_id, leave_out=(), days=DEFAULT_DAYS):
        state, record = self.load(card_id)
        if state != 'offered':
            raise ValueError('This card has no task page to add.' if state not in LIVE else 'This card already has a task page.')
        if days not in DAYS:
            raise ValueError('A task page works for 7, 14 or 30 days.')
        leave_out = sorted({p for p in (leave_out or ()) if p in task_invites.PAGE_PARTS})
        revision, action = self.draft_action(card_id)
        if not action:
            raise ValueError('The email already went out or changed. A task page goes in with an email still to send.')
        task = record['task']
        if 'summary' not in leave_out and task.get('summary') and task_invites.quotes(task['summary'], self.sources(card_id)):
            raise ValueError('The details repeat the original email, which never goes on a task page. Leave them out, or shorten them.')
        updates = [{'who': 'owner', 'what': 'shared', 'at': today()}]
        page = task_invites.page(task, leave_out, 'waiting', updates)
        pending = record.get('pending') or {}
        if pending.get('leave_out') != leave_out or pending.get('days') != days:  # a retry of the same approval reuses link and key
            sealed, key = task_invites.seal(page)
            pending = {'sealed': sealed, 'key': key, 'request_id': uuid.uuid4().hex, 'leave_out': leave_out, 'days': days}
            self.save(card_id, 'offered', {**record, 'pending': pending})
        made = self.relay().call('POST', '/task-invite', {'action': 'create', 'kind': 'handoff', 'sealed': pending['sealed'],
                                                          'request_id': pending['request_id'], 'days': days,
                                                          'check': task_invites.derived(pending['key'], 'check')})
        link = made['url'] + '#k=' + pending['key']
        try:
            self.app.store.revise_draft(str(card_id), revision, 'agent', None, task_invites.with_link(next_actions.draft_text(action), link))
        except ValueError:
            self.relay().call('POST', '/task-invite', {'action': 'revoke', 'id': made['id']})
            raise
        self.save(card_id, 'shared', {'task': task, 'to': (action.get('to') or [''])[0], 'leave_out': leave_out, 'invite_id': made['id'], 'key': pending['key'],
                                      'version': 1, 'seen': 0, 'until': str(made.get('expires_at') or '')[:10],
                                      'updates': updates, 'status': 'waiting', 'answers': []})
        return self.view(card_id)

    # -- after
    def push(self, card_id, record, done=False):
        """Seal the page again from the record and send it as the next version."""
        page = task_invites.page(record['task'], record.get('leave_out') or (), record['status'], record['updates'])
        sealed, _ = task_invites.seal(page, task_invites._unb64(record['key']))
        version = record['version'] + 1
        found = self.relay().call('POST', '/task-invite', {'action': 'update', 'id': record['invite_id'], 'sealed': sealed,
                                                          'version': version, **({'done': True} if done else {})})
        record['version'] = version
        record['until'] = str(found.get('expires_at') or record.get('until') or '')[:10]
        return record

    def stop(self, card_id):
        """Stop sharing: deleted from the Network now; the key goes too; an unsent draft loses its link line."""
        state, record = self.load(card_id)
        if state not in LIVE:
            raise ValueError('This card has no task page to stop.')
        self.relay().call('POST', '/task-invite', {'action': 'revoke', 'id': record['invite_id']})
        revision, action = self.draft_action(card_id)
        if action and 'Status and replies: ' in next_actions.draft_text(action):
            self.app.store.revise_draft(str(card_id), revision, 'agent', None, task_invites.without_link(next_actions.draft_text(action)))
        self.save(card_id, 'stopped', {'task': record['task'], 'to': record.get('to', ''), 'answers': record.get('answers', []), 'invite_id': record['invite_id']})
        return {'state': 'stopped'}

    def done(self, card_id):
        state, record = self.load(card_id)
        if state != 'shared':
            raise ValueError('This card has no open task page.')
        record['status'] = 'done'
        record['updates'].append({'who': 'owner', 'what': 'closed', 'at': today()})
        self.save(card_id, 'done', self.push(card_id, record, done=True))
        return self.view(card_id)

    def card_closed(self, card_id):
        """The owner marked the card done: its page shows it done too (and ends within 7 days). Never fails the decision."""
        try:
            if self.load(card_id)[0] == 'shared':
                self.done(card_id)
        except (ValueError, OSError, KeyError) as error:
            LOG.warning('task page done error=%s', type(error).__name__)

    def round(self):
        """New answers from live task pages: onto their cards, told to the owner, and onto the page's updates."""
        with self.app.store.connect() as db:
            db.execute(SCHEMA)
            rows = db.execute("SELECT card_id,state,record FROM task_pages WHERE state IN ('shared','done')").fetchall()
        records = {r['card_id']: (r['state'], json.loads(r['record'])) for r in rows}
        for card_id, (state, record) in list(records.items()):
            if record.get('until') and record['until'] < today():
                self.save(card_id, 'expired', {'task': record['task'], 'to': record.get('to', ''), 'answers': record.get('answers', [])})
                records.pop(card_id)
        if not records:
            return 0
        by_invite = {r['invite_id']: card_id for card_id, (_, r) in records.items()}
        found = self.relay().call('POST', '/task-invite', {'action': 'status', 'ids': list(by_invite)[:50],
                                                          'seen': {r['invite_id']: r['seen'] for _, r in records.values() if r.get('seen')}})
        count = 0
        for invite_id, replies in (found.get('replies') or {}).items():
            card_id = by_invite.get(invite_id)
            if not card_id:
                continue
            state, record = records[card_id]
            fresh = []
            for reply in sorted(replies, key=lambda r: r.get('n', 0)):
                if not isinstance(reply.get('n'), int) or reply['n'] <= record.get('seen', 0):
                    continue
                record['seen'] = reply['n']
                try:
                    answer = task_invites.open_answer(str(reply.get('sealed') or ''), record['key'])
                except (ValueError, TypeError):
                    LOG.warning('task page answer did not open')
                    continue
                answer['at'] = str(reply.get('at') or stamp())[:10]
                fresh.append(answer)
                record['answers'] = (record.get('answers') or [])[-19:] + [answer]
                record['updates'].append({'who': 'them', 'what': answer['answer'], 'at': answer['at'], **({'note': answer['note']} if answer['note'] else {})})
                if state == 'shared':
                    record['status'] = STATUS_AFTER[answer['answer']]
            if fresh and state == 'shared':
                try:
                    record = self.push(card_id, record)
                except (ValueError, OSError) as error:
                    LOG.warning('task page update error=%s', type(error).__name__)
            self.save(card_id, state, record)
            for answer in fresh:
                if answer['answer'] == 'cant':
                    self.back(card_id, answer, invite_id, record['seen'])
                count += 1
                tell = getattr(getattr(self.app, 'fleet', None), 'tell', None)  # the app's fleet_tell: phone and Telegram
                if tell:
                    note = f' "{answer["note"]}"' if answer['note'] else ''
                    tell(f'Task page for "{record["task"]["title"]}": {TOLD[answer["answer"]]}.{note}'[:200], 'task-page-' + str(card_id))
        return count

    def back(self, card_id, answer, invite_id, n):
        """Can't do this: the card comes back to Ready, the answer quoted on it. Closed by the email's send or the
        owner's Mark done, it reopens; snoozed, it wakes. Any other state (a newer choice of the owner's) stays."""
        note = f': "{answer["note"]}"' if answer.get('note') else ''
        why = f"They can't do this (task page){note}"[:400]
        decision = f'task-page-back-{invite_id}-{n}'
        store = self.app.store
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM decisions WHERE id=?', (decision,)).fetchone():
                return
            row = db.execute('SELECT status FROM suggestions WHERE id=?', (str(card_id),)).fetchone()
            if row and row['status'] == 'completed':
                store._decide(db, str(card_id), 'reopen', why, decision, 'fleet')
            elif row and row['status'] == 'snoozed':
                store._decide(db, str(card_id), 'accept', why, decision, 'fleet')

    # -- for the dashboard
    def view(self, card_id, state=None, record=None, card_status=None):
        if state is None:
            state, record = self.load(card_id)
        if not state or state == 'dismissed':
            return None
        if state == 'offered':
            if card_status not in (None, 'prepared'):
                return None
            task = record['task']
            repeats = bool(task.get('summary')) and task_invites.quotes(task['summary'], self.sources(card_id))
            return {'state': 'offered', 'repeats': repeats, 'days': DEFAULT_DAYS, 'choices': list(DAYS),
                    'preview': {k: task[k] for k in ('title', 'summary', 'due') if task.get(k)} | {'from': task_invites.first_name(task.get('from_name')) or 'You'}}
        answers = [{'answer': a['answer'], 'note': a.get('note', ''), 'at': a.get('at', '')} for a in (record.get('answers') or [])][-5:]
        return {'state': state, 'until': record.get('until', ''), 'answers': answers, 'lead': lead(record.get('to'), answers, state)}

    def annotate(self, db, suggestions):
        db.execute(SCHEMA)
        rows = {r['card_id']: r for r in db.execute('SELECT card_id,state,record FROM task_pages')}
        for s in suggestions:
            row = rows.get(str(s.get('id')))
            if row:
                shown = self.view(s['id'], row['state'], json.loads(row['record']), s.get('status'))
                if shown:
                    s['task_page'] = shown

    def action(self, name, args):
        card_id = str(args.get('suggestion_id') or '')
        if name == 'task_page_share':
            days = args.get('days', DEFAULT_DAYS)
            return self.share(card_id, args.get('leave_out') or (), days if isinstance(days, int) else DEFAULT_DAYS)
        if name == 'task_page_dismiss':
            return self.dismiss(card_id)
        if name == 'task_page_stop':
            return self.stop(card_id)
        raise ValueError('Unknown task page action')
