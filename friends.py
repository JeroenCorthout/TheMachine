"""Friends: people whose Machines help each other one to one, next to Work and Family Fleets (docs/fleet.md).

Someone adds a friend by their username (for a hosted Machine, its address name). The other side accepts or declines
on their own Machine (the Fleet tab, Telegram or the phone) or their account page; nothing connects before that. Then
each friend is one more group in the relay's directory (kind 'friends'), with the same per-friend rules as a teammate
(Ask me first by default, Without names by default), and kept strictly apart from the owner's Fleets.

Friends ask four things: who knows someone, meeting context (only for a meeting with that friend), warm intros, and
free/busy times. Free/busy is answered from the owner's own calendars by plain code: busy blocks only, start and end,
never what, where or with whom. A friend's words and answers are untrusted data, kept to fixed shapes; no model, tool
or Do it runs on them."""
from __future__ import annotations

import datetime as dt
import json
import re

KIND = 'friends'
KINDS = ('relationships', 'meeting', 'intro', 'availability')
MAX_DAYS, MAX_BLOCKS = 14, 300
SLOT = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z')
USERNAME = re.compile(r'[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])')
ACTIONS = ('add', 'withdraw', 'accept', 'decline', 'block', 'remove', 'unblock')


def parse_availability(question):
    """A friend's free/busy question in its fixed form ({start: a day, days: 1 to 14}), else None."""
    if not isinstance(question, dict):
        return None
    start, days = question.get('start'), question.get('days')
    if not isinstance(start, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', start):
        return None
    try:
        dt.date.fromisoformat(start)
    except ValueError:
        return None
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_DAYS:
        return None
    return {'start': start, 'days': days}


def clean_availability(answer):
    """A friend's free/busy answer: busy blocks only, [start, end] in UTC minutes. Anything else is dropped."""
    if not isinstance(answer, dict) or not isinstance(answer.get('busy'), list):
        return None
    busy = [[b[0], b[1]] for b in answer['busy'][:MAX_BLOCKS]
            if isinstance(b, list) and len(b) == 2 and all(isinstance(x, str) and SLOT.fullmatch(x) for x in b) and b[0] < b[1]]
    return {'busy': busy}


def _slot(stamp):
    return stamp.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')


def free_busy(db, config, question):
    """The owner's busy blocks for the days asked, from their own calendars: start and end only. All-day events,
    canceled ones, ones they declined, and calendars shared with them by someone else don't count. Overlaps merge."""
    start = dt.datetime.fromisoformat(question['start']).replace(tzinfo=dt.timezone.utc) - dt.timedelta(hours=14)
    end = start + dt.timedelta(days=question['days'], hours=28)
    own = [a['id'] for a in config.get('accounts', [])]
    if not own:
        return {'busy': []}
    blocks = []
    for row in db.execute("SELECT occurred_at,metadata FROM events WHERE kind='calendar.event' AND deleted=0 AND account_id IN (SELECT value FROM json_each(?)) "
                          'AND occurred_at<? AND occurred_at>=?', (json.dumps(list(own)), end.isoformat(), (start - dt.timedelta(days=2)).isoformat())):
        m = json.loads(row['metadata'] or '{}')
        if m.get('all_day') or m.get('status') == 'cancelled' or m.get('access') == 'shared_read_only':
            continue
        if any(isinstance(a, dict) and a.get('self') and a.get('responseStatus') == 'declined' for a in m.get('attendees') or []):
            continue
        try:
            a = dt.datetime.fromisoformat(str(row['occurred_at']).replace('Z', '+00:00'))
            b = dt.datetime.fromisoformat(str(m.get('end_at') or '').replace('Z', '+00:00'))
        except ValueError:
            continue
        if a.tzinfo is None or b.tzinfo is None or b <= a or b <= start or a >= end:
            continue
        blocks.append([max(a, start), min(b, end)])
    blocks.sort()
    merged = []
    for a, b in blocks:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return {'busy': [[_slot(a), _slot(b)] for a, b in merged if _slot(a) < _slot(b)][:MAX_BLOCKS]}


def question_text(question):
    days = question.get('days', 1)
    return f"When are you busy, {question.get('start', '?')}" + (f' and the {days - 1} days after?' if days > 1 else '?')


def answer_text(answer):
    busy = (answer or {}).get('busy') or []
    if not busy:
        return 'free the whole time'
    shown = ', '.join(f'{a[5:10]} {a[11:16]}–{b[11:16] if a[:10] == b[:10] else b[5:10] + " " + b[11:16]} UTC' for a, b in busy[:6])
    return f'busy {len(busy)} time{"s" if len(busy) != 1 else ""}: {shown}' + (' …' if len(busy) > 6 else '')


class Friends:
    """Mixed into fleet.Fleet: the owner's friends on their own Machine."""

    def friends_ref(self):
        return next((ref for ref, f in self.fleets().items() if f.get('kind') == KIND), None)

    def friends_view(self):
        """The friends group as the Fleet tab shows it: username, requests in and out, and whom the owner blocked."""
        ref = self.friends_ref()
        found = self.fleets().get(ref) if ref else None
        if not found:
            return None
        from fleet import clean
        requests = found.get('requests') if isinstance(found.get('requests'), dict) else {}
        return {'id': ref, 'username': clean(found.get('username'), 40),
                'in': [{'id': clean(r.get('id'), 40), 'name': clean(r.get('name'), 120), 'at': clean(r.get('at'), 40)}
                       for r in (requests.get('in') or [])[:50] if isinstance(r, dict)],
                'out': [{'id': clean(r.get('id'), 40), 'username': clean(r.get('username'), 40), 'at': clean(r.get('at'), 40)}
                        for r in (requests.get('out') or [])[:50] if isinstance(r, dict)],
                'blocked': [{'member_id': clean(b.get('member_id'), 40), 'name': clean(b.get('name'), 120)}
                            for b in (found.get('blocked') or [])[:100] if isinstance(b, dict)]}

    def friend_action(self, action, username='', rid='', member_id=''):
        """The owner's own: add someone by username, withdraw a request, accept, decline or block one, remove, block or
        unblock a friend. Relayed as is; the relay answers an added request the same whether that name is anyone's."""
        if action not in ACTIONS:
            raise ValueError('Unknown friends action')
        body = {'action': action}
        if action == 'add':
            name = str(username or '').strip().lower().lstrip('@')
            if not USERNAME.fullmatch(name):
                raise ValueError('A username is 3 to 30 letters (a-z), digits or hyphens')
            body['username'] = name
        elif rid:
            body['id'] = str(rid)
        elif member_id:
            body['member_id'] = str(member_id)
        else:
            raise ValueError('Which request or friend?')
        found = self.relay.call('POST', '/friends', body, timeout=30)
        self.directory(refresh=True)
        with self.connect() as db:
            self.log(db, 'out', self.friends_ref() or KIND, None, body.get('username') or 'a friend', 'friend', f'friends: {action}', 'done')
        return found if isinstance(found, dict) else {'ok': True}

    def ask_availability(self, member_id, start, days):
        """Ask one friend's Machine when they are busy: their answer is busy blocks only."""
        ref = self.friends_ref()
        if not ref or not self.member(ref, member_id):
            raise ValueError('Not one of your friends')
        question = parse_availability({'start': start, 'days': days})
        if not question:
            raise ValueError(f'Pick a day and 1 to {MAX_DAYS} days')
        return self.ask('availability', question, fleet=ref, to=[member_id])

    def friends_meeting_round(self):
        """With meeting questions on: ask a friend about the other people in an open card's meeting this week that
        friend is in too (the relay only carries meeting questions to a friend who is among the attendees)."""
        ref = self.friends_ref()
        friends = self.teammates(ref) if ref else []
        if not friends:
            return 0
        from fleet import EMAIL, MEETING_DAYS, OPEN_CARD, account_emails
        mine = {e.lower() for e in account_emails(self.config)} | {str(a.get('email') or '').lower() for a in self.config.get('accounts', [])}
        by_email = {e.lower(): m for m in friends for e in m.get('emails', []) if isinstance(e, str)}
        today = dt.datetime.now(dt.timezone.utc)
        todo = []
        with self.connect() as db:
            cards = db.execute(f"SELECT id,event_ids FROM suggestions WHERE status IN ({','.join('?' * len(OPEN_CARD))}) "
                               "AND id NOT IN (SELECT substr(context,6) FROM fleet_out WHERE context LIKE 'card:%' AND fleet=?)",
                               OPEN_CARD + (ref,)).fetchall()
            for card in cards:
                ids = json.loads(card['event_ids'] or '[]')
                if not ids:
                    continue
                for event in db.execute("SELECT metadata FROM events WHERE kind='calendar.event' AND deleted=0 AND id IN (SELECT value FROM json_each(?))", (json.dumps(ids),)):
                    m = json.loads(event['metadata'] or '{}')
                    when = str((m.get('start') or {}).get('dateTime') or (m.get('start') or {}).get('date') or '') if isinstance(m.get('start'), dict) else ''
                    try:
                        at = dt.datetime.fromisoformat(when.replace('Z', '+00:00'))
                        at = at if at.tzinfo else at.replace(tzinfo=dt.timezone.utc)
                    except ValueError:
                        continue
                    if not today <= at <= today + dt.timedelta(days=MEETING_DAYS) or m.get('status') == 'cancelled':
                        continue
                    people = sorted({str(a.get('email') or '').lower() for a in m.get('attendees') or [] if isinstance(a, dict)
                                     and not a.get('self') and not a.get('resource') and EMAIL.fullmatch(str(a.get('email') or '').lower())} - mine)
                    friend = next((by_email[e] for e in people if e in by_email), None)
                    if friend and len(people) <= 20:
                        todo.append((card['id'], friend['member_id'], {'emails': people, 'when': when[:10]}))
                        break
        for card_id, member_id, question in todo[:10]:
            self.ask('meeting', question, context='card:' + card_id, fleet=ref, to=[member_id])
        return len(todo[:10])
