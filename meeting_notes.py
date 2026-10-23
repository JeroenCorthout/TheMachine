"""Meeting notes (a Zoom AI Companion summary, connectors/zoom.py) and the calendar meeting they belong to.

Matched by plain code, never by a model: the Zoom meeting ID in the calendar event (its join link or "Meeting ID" in the
location or description) at about the same time; else the time alone (start within a few minutes, or mostly
overlapping), preferring the event the notes' host was in. Two events that fit equally well match neither: no notes is
better than another meeting's notes. What the notes say is read by the review, which judges it by meaning."""
from __future__ import annotations

import datetime as dt
import json
import re

KINDS = ('zoom.summary',)
SOURCE = {'zoom.summary': 'Zoom'}
SAME_ID_HOURS = 3  # a recurring Zoom meeting keeps its ID: the same ID only counts at about the same time
NEAR_MINUTES = 20
LINK = re.compile(r'zoom\.us/(?:j|w|s|wc/join)/(\d{9,12})\b', re.I)
SPOKEN = re.compile(r'Meeting\s+ID\s*:?\s*(\d{3}[\s-]?\d{3,4}[\s-]?\d{3,5})\b', re.I)


def stamp(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def zoom_ids(*texts):
    """The Zoom meeting IDs a calendar event names, as digits."""
    found = set()
    for text in texts:
        text = str(text or '')
        found |= set(LINK.findall(text))
        found |= {re.sub(r'\D', '', m) for m in SPOKEN.findall(text)}
    return found


def _span(event):
    m = event['metadata'] if isinstance(event['metadata'], dict) else json.loads(event['metadata'] or '{}')
    start = stamp(event['occurred_at'])
    end = stamp(m.get('end_at')) or (start + dt.timedelta(hours=1) if start else None)
    return start, end, m


def _people(m):
    emails = {str((m.get('organizer') or {}).get('email') or '').lower()}
    emails |= {str(a.get('email') or '').lower() for a in m.get('attendees') or [] if isinstance(a, dict)}
    return emails - {''}


def candidates(db, start):
    """Calendar events around the notes' start (a day either side; times are compared parsed, not as text)."""
    rows = db.execute("SELECT id,source_id,title,body,occurred_at,metadata FROM events WHERE kind='calendar.event' AND deleted=0 "
                      'AND occurred_at>=? AND occurred_at<=?',
                      ((start - dt.timedelta(days=1)).isoformat(), (start + dt.timedelta(days=1)).isoformat())).fetchall()
    return [dict(r) for r in rows]


def match(db, notes):
    """The calendar event (a dict with id, title, occurred_at, metadata) these notes are of, or None."""
    m = notes['metadata'] if isinstance(notes['metadata'], dict) else json.loads(notes['metadata'] or '{}')
    start = stamp(m.get('start_at')) or stamp(notes['occurred_at'])
    if not start:
        return None
    end = stamp(m.get('end_at')) or start + dt.timedelta(hours=1)
    meeting_id = re.sub(r'\D', '', str(m.get('meeting_id') or ''))
    host = str(m.get('host_email') or '').lower()
    by_id, by_time = [], []
    for event in candidates(db, start):
        begun, ended, em = _span(event)
        if not begun or em.get('all_day') or em.get('status') == 'cancelled':
            continue
        if meeting_id and meeting_id in zoom_ids(em.get('location'), event.get('body')) and abs(begun - start) <= dt.timedelta(hours=SAME_ID_HOURS):
            by_id.append((abs(begun - start), event))
            continue
        overlap = (min(end, ended) - max(start, begun)).total_seconds()
        if abs(begun - start) <= dt.timedelta(minutes=NEAR_MINUTES) or overlap >= 0.5 * max(60, (end - start).total_seconds()):
            by_time.append((host in _people(em), event))
    if by_id:
        by_id.sort(key=lambda x: x[0])
        return by_id[0][1] if len(by_id) == 1 or by_id[0][0] < by_id[1][0] else None
    if host and any(hosted for hosted, _ in by_time):
        by_time = [x for x in by_time if x[0]]
    return by_time[0][1] if len(by_time) == 1 else None


def for_event(db, event_id, day):
    """The newest notes that match calendar event `event_id` (its day as YYYY-MM-DD), or None."""
    start = dt.date.fromisoformat(day)
    rows = db.execute(f"SELECT id,kind,title,occurred_at,metadata FROM events WHERE kind IN ({','.join('?' * len(KINDS))}) AND deleted=0 "
                      'AND occurred_at>=? AND occurred_at<? ORDER BY occurred_at DESC',
                      (*KINDS, (start - dt.timedelta(days=1)).isoformat(), (start + dt.timedelta(days=2)).isoformat())).fetchall()
    for notes in rows:
        found = match(db, dict(notes))
        if found and found['id'] == event_id:
            return dict(notes)
    return None
