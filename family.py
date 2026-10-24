"""Family Fleets: the Machines of the people someone lives or plans with help each other (docs/family.md).

A Family Fleet asks three things, all over the Network:

- availability: "when are we all free?" Each member's Machine answers from its owner's own calendars with busy blocks
  only (friends.free_busy): never what, where or with whom. The asking Machine finds the common free times by plain code.
- todo: "can you pick up Sam on Friday?" A to-do for one member. Their Machine shows it as a card to accept or decline;
  once accepted it is theirs, with a reminder on its due day, and the asker sees accepted, declined or done.
- event: "dinner at grandma's, Sunday 18:00?" A plan for some or all members. Each Machine shows it as a card to say
  yes or no. Saying yes never writes to a calendar: the card has an "Add to my calendar" link the owner opens themselves.

Rules are per kind for the Family, the same choices as a Work Fleet (Ask me first, Always, Never), with friendlier
defaults: free/busy is answered by itself (busy blocks only), to-dos and plans wait for the owner. Work questions
(who knows someone, meeting context, intros, handoffs, follow-ups, the brief, free questions) never travel in a Family:
the Network refuses them and this Machine doesn't ask or answer them there.

Safety: a member's words are untrusted data, kept to fixed shapes (parse) and only shown, quoted. No model, tool or
Do it runs on them; nothing is written anywhere until the owner accepts."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
import urllib.parse

KIND = 'family'
KINDS = ('availability', 'todo', 'event')
REQUESTS = ('todo', 'event')
DEFAULT_RULES = {'availability': 'always', 'todo': 'ask', 'event': 'ask'}
WORK_ONLY = ('relationships', 'meeting', 'handling', 'intro', 'handoff', 'followup', 'brief', 'anything')
LABELS = {'todo': 'to-dos', 'event': 'family plans'}
TITLE, NOTE, PLACE = 120, 300, 120
STAGES = {'accepted': 'accepted', 'declined': 'declined', 'done': 'done'}
FREE_FROM, FREE_UNTIL, FREE_MINUTES = 8, 21, 60  # common free times: within these local hours, at least this long
SHOWN_DAYS = 14
DAY = re.compile(r'\d{4}-\d{2}-\d{2}')
MOMENT = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?')


def _text(value, chars):
    return ' '.join(value.split())[:chars] if isinstance(value, str) else ''


def _when(value):
    """A date (2026-10-24) or a moment to the minute (2026-10-24T18:00, with or without its offset), else None."""
    if not isinstance(value, str):
        return None
    value = value.strip()[:25]
    try:
        if DAY.fullmatch(value):
            dt.date.fromisoformat(value)
            return value
        if MOMENT.fullmatch(value):
            dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            offset = re.search(r'(Z|[+-]\d{2}:\d{2})$', value)
            return value[:16] + (offset.group(1) if offset else '')
    except ValueError:
        return None
    return None


def parse(kind, question):
    """A member's to-do or plan in its fixed form, else None."""
    if not isinstance(question, dict):
        return None
    title, note = _text(question.get('title'), TITLE), _text(question.get('note'), NOTE)
    if not title:
        return None
    if kind == 'todo':
        due = _when(question.get('due'))
        return {'title': title, **({'note': note} if note else {}), **({'due': due} if due else {})}
    if kind == 'event':
        start, end, place = _when(question.get('start')), _when(question.get('end')), _text(question.get('place'), PLACE)
        if not start or (question.get('end') and not end):
            return None
        if end:
            if (len(start) == 10) != (len(end) == 10):
                return None
            a = dt.date.fromisoformat(start) if len(start) == 10 else dt.datetime.fromisoformat(start.replace('Z', '+00:00'))
            b = dt.date.fromisoformat(end) if len(end) == 10 else dt.datetime.fromisoformat(end.replace('Z', '+00:00'))
            if isinstance(a, dt.datetime) and bool(a.tzinfo) != bool(b.tzinfo):
                return None
            if b < a:
                return None
        return {'title': title, 'start': start, **({'end': end} if end else {}), **({'place': place} if place else {}),
                **({'note': note} if note else {})}
    return None


def clean_answer(kind, answer):
    if not isinstance(answer, dict) or answer.get('stage') not in STAGES:
        return None
    return {'stage': answer['stage']}


def _shown(value):
    try:
        if len(value) == 10:
            return dt.date.fromisoformat(value).strftime('%a, %b %-d')
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).strftime('%a, %b %-d %H:%M')
    except (TypeError, ValueError):
        return str(value or '')


def question_text(kind, question):
    if kind == 'todo':
        return f"To-do: {question.get('title', '?')}" + (f", due {_shown(question['due'])}" if question.get('due') else '') + \
               (f" (their note: “{question['note']}”)" if question.get('note') else '')
    return (f"Plan: {question.get('title', '?')}, {_shown(question.get('start'))}" + (f" at {question['place']}" if question.get('place') else '')
            + (f" (their note: “{question['note']}”)" if question.get('note') else ''))


def answer_text(kind, answer):
    stage = (answer or {}).get('stage')
    if kind == 'event':
        return {'accepted': 'is in', 'declined': 'can’t make it', 'done': 'is in'}.get(stage, 'no answer')
    return STAGES.get(stage, 'no answer')


def calendar_link(question):
    """A Google Calendar "add event" link the owner opens themselves: nothing is written by this Machine."""
    def stamp(value, end=False):
        if len(value) == 10:
            day = dt.date.fromisoformat(value) + dt.timedelta(days=1 if end else 0)
            return day.strftime('%Y%m%d')
        moment = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo:
            return moment.astimezone(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        return moment.strftime('%Y%m%dT%H%M%S')
    start, end = question['start'], question.get('end')
    if not end and len(start) == 10:
        end = start
    elif not end:  # an hour, by default
        moment = dt.datetime.fromisoformat(start.replace('Z', '+00:00')) + dt.timedelta(hours=1)
        end = moment.isoformat()
    params = {'action': 'TEMPLATE', 'text': question['title'], 'dates': f'{stamp(start)}/{stamp(end, True)}'}
    if question.get('place'):
        params['location'] = question['place']
    if question.get('note'):
        params['details'] = question['note']
    return 'https://calendar.google.com/calendar/render?' + urllib.parse.urlencode(params)


def common_free(busy_lists, start, days, tz, hours=(FREE_FROM, FREE_UNTIL), minutes=FREE_MINUTES):
    """The times everyone is free: within `hours` local time on each day, at least `minutes` long. busy_lists holds each
    member's busy blocks ([start, end] UTC slots, as friends.free_busy gives them). Plain code."""
    blocks = []
    for busy in busy_lists:
        for a, b in busy or []:
            try:
                blocks.append((dt.datetime.fromisoformat(a.replace('Z', '+00:00')), dt.datetime.fromisoformat(b.replace('Z', '+00:00'))))
            except (AttributeError, ValueError):
                continue
    blocks.sort()
    out = []
    first = dt.date.fromisoformat(start)
    for n in range(days):
        day = first + dt.timedelta(days=n)
        cursor = dt.datetime.combine(day, dt.time(hours[0]), tz)
        close = dt.datetime.combine(day, dt.time(hours[1]), tz)
        for a, b in blocks:
            if b <= cursor or a >= close:
                continue
            if (a - cursor).total_seconds() >= minutes * 60:
                out.append([cursor, a])
            cursor = max(cursor, b)
        if (close - cursor).total_seconds() >= minutes * 60:
            out.append([cursor, close])
    return [[a.astimezone(tz).isoformat(timespec='minutes'), b.astimezone(tz).isoformat(timespec='minutes')] for a, b in out]


def free_text(slot):
    a, b = (dt.datetime.fromisoformat(x) for x in slot)
    return f"{a:%a, %b} {a.day} {a:%H:%M}–{b:%H:%M}"


class Family:
    """Mixed into fleet.Fleet: the owner's Family Fleet on their own Machine."""

    def family_ref(self):
        return next((ref for ref, f in self.fleets().items() if f['kind'] == KIND), None)

    def _family(self):
        ref = self.family_ref()
        if not ref:
            raise ValueError('You are not in a Family Fleet')
        if not self.teammates(ref):
            raise ValueError('Nobody else in your Family yet')
        return ref

    def request_context(self, prefix, proposal):
        """Keep a group token across a failed ask; clear it only after the local receipt is saved."""
        key = 'pending_context:' + hashlib.sha256(json.dumps([prefix, proposal], sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            row = db.execute('SELECT value FROM fleet_meta WHERE key=?', (key,)).fetchone()
            token = json.loads(row[0]) if row else secrets.token_hex(8)
            if not row:
                db.execute('INSERT INTO fleet_meta(key,value) VALUES(?,?)', (key, json.dumps(token)))
        return key, prefix + token

    def ask_context(self, kind, question, prefix, ref, to=None):
        key, context = self.request_context(prefix, [ref, kind, question, sorted(to or [])])
        sent = self.ask(kind, question, context=context, fleet=ref, to=to, context_key=key)
        return sent, context

    def ask_family_free(self, start, days):
        """Ask everyone in the Family when they are busy; the common free times show once they answer."""
        import friends
        ref = self._family()
        question = friends.parse_availability({'start': start, 'days': days})
        if not question:
            raise ValueError(f'Pick a day and 1 to {friends.MAX_DAYS} days')
        sent, context = self.ask_context('availability', question, 'family-free:', ref)
        return {**sent, 'id': context.split(':', 1)[1]}

    def propose_todo(self, member_id, title, due=None, note=''):
        ref = self._family()
        if not self.member(ref, member_id):
            raise ValueError('Not in your Family')
        question = parse('todo', {'title': title, 'due': due or None, 'note': note})
        if not question:
            raise ValueError('Say what needs doing')
        if due and 'due' not in question:
            raise ValueError('Due: a date, or a date and time')
        return self.ask_context('todo', question, 'family-todo:', ref, to=[member_id])[0]

    def propose_event(self, title, start, end=None, place='', note='', to=None):
        ref = self._family()
        question = parse('event', {'title': title, 'start': start, 'end': end or None, 'place': place, 'note': note})
        if not question:
            raise ValueError('Give the plan a name and a start')
        wanted = [m for m in (to or []) if self.member(ref, m)] or None
        if to and not wanted:
            raise ValueError('Not in your Family')
        return self.ask_context('event', question, 'family-event:', ref, to=wanted)[0]

    def family_card(self, row, question, sender):
        """The card for a to-do or plan the owner accepted: theirs now, made by plain code from the member's words."""
        if row['kind'] == 'todo':
            return self.make_card(kind='todo', ask_id=row['id'], name=sender, title=f'{question["title"]} (for {sender})'[:90],
                                  body=(f'{sender} asked you through your Family: “{question["title"]}”'
                                        + (f' Their note: “{question["note"]}”' if question.get('note') else '')
                                        + (f' Due {_shown(question["due"])}.' if question.get('due') else '')),
                                  evidence=[], due=(question.get('due') or '')[:10] or None, family=True)
        link = calendar_link(question)
        return self.make_card(kind='event', ask_id=row['id'], name=sender, title=f'{question["title"]}, {_shown(question["start"])}'[:90],
                              body=(f'You said yes to {sender}’s plan: “{question["title"]}”, {_shown(question["start"])}'
                                    + (f' at {question["place"]}' if question.get('place') else '') + '.'
                                    + (f' Their note: “{question["note"]}”' if question.get('note') else '')
                                    + f' Add it to your calendar yourself: {link}'),
                              evidence=[], due=question['start'][:10], family=True)

    def family_reminders(self, today=None):
        """An accepted to-do, on its due day: one reminder on this Machine (phone and Telegram), once."""
        if not self.tell:
            return 0
        today = today or self.local_now().date().isoformat()
        told = 0
        with self.connect() as db:
            rows = db.execute("SELECT r.id,r.name,i.question,s.status AS card FROM fleet_requests r JOIN fleet_in i ON i.id=r.id "
                              "LEFT JOIN suggestions s ON s.id=r.card_id WHERE r.kind='todo' AND r.stage='accepted'").fetchall()
            due = [r for r in rows if (json.loads(r['question']).get('due') or '9999')[:10] <= today and r['card'] not in ('completed', 'rejected')
                   and not db.execute('SELECT 1 FROM fleet_meta WHERE key=?', ('todo_reminded:' + r['id'],)).fetchone()]
            for r in due:
                db.execute('INSERT OR IGNORE INTO fleet_meta(key,value) VALUES(?,?)', ('todo_reminded:' + r['id'], json.dumps(today)))
        for r in due:
            q = json.loads(r['question'])
            try:
                self.tell(f"Reminder: {q['title']} (for {r['name']}) is due {'today' if (q.get('due') or '')[:10] == today else _shown(q['due'])}.", r['id'])
                told += 1
            except Exception:  # a reminder that can't go out now is not retried: the card still shows it
                pass
        return told

    def family_view(self):
        ref = self.family_ref()
        if not ref:
            return None
        import friends
        tz = self.local_now().tzinfo
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=SHOWN_DAYS)).isoformat(timespec='seconds')
        with self.connect() as db:
            rows = db.execute("SELECT * FROM fleet_out WHERE fleet=? AND context LIKE 'family-%' AND at>=? ORDER BY at DESC LIMIT 200",
                              (ref, since)).fetchall()
            mine = {}
            groups = {}
            for r in rows:
                kind, token = r['context'].split(':', 1)
                group = groups.setdefault(r['context'], {'id': token, 'kind': kind[7:], 'at': r['at'], 'question': json.loads(r['question']), 'answers': []})
                answer = json.loads(r['answer']) if r['answer'] else None
                group['answers'].append({'member_id': r['member_id'], 'name': r['name'], 'status': r['status'], 'answer': answer})
        free, todos, plans = [], [], []
        for g in groups.values():
            q = g['question']
            if g['kind'] == 'free':
                answered = [a for a in g['answers'] if a['status'] == 'answered' and a['answer']]
                if g['id'] not in mine:
                    with self.connect() as db:
                        mine[g['id']] = friends.free_busy(db, self.config, q).get('busy', [])
                slots = common_free([mine[g['id']], *[a['answer'].get('busy') for a in answered]], q['start'], q['days'], tz)
                free.append({'id': g['id'], 'at': g['at'], 'start': q['start'], 'days': q['days'], 'answered': len(answered),
                             'asked': len(g['answers']), 'waiting': [a['name'] for a in g['answers'] if a['status'] in ('asked', 'waiting')],
                             'slots': [{'from': a, 'until': b, 'text': free_text([a, b])} for a, b in slots[:12]]})
            elif g['kind'] == 'todo':
                a = g['answers'][0]
                stage = (a['answer'] or {}).get('stage') or {'asked': 'asked', 'waiting': 'waiting for them'}.get(a['status'], a['status'])
                todos.append({'id': g['id'], 'at': g['at'], 'title': q['title'], 'due': q.get('due'), 'due_text': _shown(q['due']) if q.get('due') else '',
                              'name': a['name'], 'stage': stage})
            elif g['kind'] == 'event':
                yes = [a['name'] for a in g['answers'] if (a['answer'] or {}).get('stage') == 'accepted']
                no = [a['name'] for a in g['answers'] if a['status'] in ('declined', 'expired') or (a['answer'] or {}).get('stage') == 'declined']
                plans.append({'id': g['id'], 'at': g['at'], 'title': q['title'], 'start': q['start'], 'when': _shown(q['start']),
                              'place': q.get('place', ''), 'yes': yes, 'no': no,
                              'waiting': [a['name'] for a in g['answers'] if a['name'] not in yes and a['name'] not in no],
                              'link': calendar_link(q)})
        with self.connect() as db:
            incoming = [{'id': r['id'], 'name': r['name'], 'title': json.loads(r['question'])['title'], 'stage': r['stage'],
                         'due': json.loads(r['question']).get('due') or json.loads(r['question']).get('start')}
                        for r in db.execute("SELECT r.id,r.name,r.stage,i.question FROM fleet_requests r JOIN fleet_in i ON i.id=r.id "
                                            "WHERE r.kind='todo' AND r.fleet=? AND r.stage IN ('accepted','waiting') ORDER BY r.at DESC LIMIT 30", (ref,))]
        return {'id': ref, 'free': free[:5], 'todos': todos[:20], 'plans': plans[:10], 'mine': incoming}
